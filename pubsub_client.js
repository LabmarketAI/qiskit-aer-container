/**
 * pubsub_client.js — Browser ES-module client for jupyter_pubsub
 *
 * Usage
 * ─────
 *   import { PubSubClient } from './pubsub_client.js';
 *
 *   const client = new PubSubClient('localhost', 8888);
 *
 *   // List active cells
 *   const { cells } = await client.listCells();
 *
 *   // Stream live output, with automatic chunk reassembly
 *   const unsub = client.subscribe('nx-graph', (envelope) => {
 *     console.log(envelope.data);
 *   });
 *
 *   // Stop streaming
 *   unsub();
 *
 * Chunk reassembly
 * ────────────────
 * Each WebSocket message is a JSON envelope:
 *   { cell_name, kernel_id, msg_id, msg_type,
 *     chunk_id, total_chunks, data, metadata }
 *
 * When total_chunks === 1 the callback fires immediately.
 * When total_chunks > 1, chunks are buffered by msg_id until all arrive,
 * then the callback fires once with data = concatenated parts.
 */

export class PubSubClient {
  /**
   * @param {string} host - Jupyter server hostname (default: 'localhost')
   * @param {number} port - Jupyter server port (default: 8888)
   */
  constructor(host = 'localhost', port = 8888) {
    this._http = `http://${host}:${port}`;
    this._ws   = `ws://${host}:${port}`;
    /** @type {Map<string, { ws: WebSocket, chunks: Map<string, ChunkState> }>} */
    this._subs = new Map();
  }

  // ── REST helpers ──────────────────────────────────────────────────────────

  /**
   * List all known pub/sub cells (active + historical).
   * @returns {Promise<{ cells: string[], subscriber_counts: Object, kernel_count: number }>}
   */
  async listCells() {
    return this._get('/pubsub/cells');
  }

  /**
   * Get detail for a specific cell.
   * @param {string} cellName
   * @returns {Promise<Object>}
   */
  async getCellInfo(cellName) {
    return this._get(`/pubsub/cells/${encodeURIComponent(cellName)}`);
  }

  /**
   * Fetch recent message history for a cell.
   * @param {string} cellName
   * @param {number} [limit=20] - Max messages to return (1–50)
   * @returns {Promise<{ cell_name: string, count: number, messages: Object[] }>}
   */
  async getHistory(cellName, limit = 20) {
    return this._get(`/pubsub/history/${encodeURIComponent(cellName)}?limit=${limit}`);
  }

  /**
   * Fetch the MCP server manifest.
   * @returns {Promise<Object>}
   */
  async getMCPManifest() {
    return this._get('/pubsub/mcp');
  }

  // ── WebSocket subscription ────────────────────────────────────────────────

  /**
   * Subscribe to live output from a tagged cell.
   *
   * Multi-chunk messages are transparently reassembled before the callback
   * fires, so the caller always receives a complete envelope.
   *
   * @param {string}   cellName - Tag name used in the notebook (e.g. 'nx-graph')
   * @param {function} callback - Called with a complete envelope object
   * @param {Object}   [opts]
   * @param {boolean}  [opts.reconnect=true] - Reconnect automatically on close
   * @param {number}   [opts.reconnectDelay=2000] - ms to wait before reconnecting
   * @returns {function} Unsubscribe function — call it to close the WebSocket
   */
  subscribe(cellName, callback, { reconnect = true, reconnectDelay = 2000 } = {}) {
    let active = true;

    const connect = () => {
      if (!active) return;

      const ws = new WebSocket(`${this._ws}/pubsub/ws/${encodeURIComponent(cellName)}`);
      /** @type {Map<string, { parts: Array, received: number, total: number }>} */
      const chunks = new Map();

      ws.onopen = () => {
        console.debug(`[pubsub] connected to '${cellName}'`);
      };

      ws.onmessage = (event) => {
        let envelope;
        try {
          envelope = JSON.parse(event.data);
        } catch {
          console.warn('[pubsub] failed to parse message', event.data);
          return;
        }

        const { chunk_id, total_chunks } = envelope;

        // Fast path: single-chunk message
        if (total_chunks === 1) {
          callback(envelope);
          return;
        }

        // Multi-chunk reassembly keyed by msg_id (falls back to chunk_id sequence)
        const key = envelope.msg_id || `${envelope.kernel_id}:${envelope.msg_type}`;
        if (!chunks.has(key)) {
          chunks.set(key, { parts: new Array(total_chunks), received: 0, total: total_chunks });
        }
        const state = chunks.get(key);
        state.parts[chunk_id - 1] = envelope.data;
        state.received += 1;

        if (state.received === state.total) {
          chunks.delete(key);
          callback({
            ...envelope,
            data: state.parts.join(''),
            chunk_id: 1,
            total_chunks: 1,
          });
        }
      };

      ws.onerror = (err) => {
        console.error(`[pubsub] WebSocket error for '${cellName}'`, err);
      };

      ws.onclose = (ev) => {
        console.debug(`[pubsub] disconnected from '${cellName}' (code ${ev.code})`);
        if (active && reconnect) {
          setTimeout(connect, reconnectDelay);
        }
      };

      this._subs.set(cellName, { ws, chunks });
    };

    connect();

    // Return unsubscribe function
    return () => {
      active = false;
      const sub = this._subs.get(cellName);
      if (sub) {
        sub.ws.close(1000, 'unsubscribed');
        this._subs.delete(cellName);
      }
    };
  }

  /**
   * Unsubscribe from a cell by name.
   * @param {string} cellName
   */
  unsubscribe(cellName) {
    const sub = this._subs.get(cellName);
    if (sub) {
      sub.ws.close(1000, 'unsubscribed');
      this._subs.delete(cellName);
    }
  }

  /**
   * Close all active WebSocket subscriptions.
   */
  unsubscribeAll() {
    for (const cellName of [...this._subs.keys()]) {
      this.unsubscribe(cellName);
    }
  }

  // ── MCP JSON-RPC helper ───────────────────────────────────────────────────

  /**
   * Call an MCP tool by name.
   *
   * @param {string} toolName - e.g. 'list_cells', 'get_history', 'get_ws_url'
   * @param {Object} [args={}] - Tool arguments
   * @returns {Promise<Object>} Parsed tool result
   *
   * @example
   * const result = await client.callMCPTool('get_history', { cell_name: 'nx-graph', limit: 5 });
   * const messages = JSON.parse(result.content[0].text).messages;
   */
  async callMCPTool(toolName, args = {}) {
    const body = {
      jsonrpc: '2.0',
      id: Date.now(),
      method: 'tools/call',
      params: { name: toolName, arguments: args },
    };
    const resp = await fetch(`${this._http}/pubsub/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const json = await resp.json();
    if (json.error) throw new Error(`MCP error: ${json.error.message}`);
    return json.result;
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  async _get(path) {
    const resp = await fetch(`${this._http}${path}`);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`GET ${path} → ${resp.status}: ${text}`);
    }
    return resp.json();
  }
}
