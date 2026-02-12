# Start the container, reusing cached image layers
up:
	@docker compose up -d
	@echo ""
	@echo "Jupyter Lab is running at:"
	@echo ""
	@echo "  http://localhost:8888"
	@echo ""

# Stop and remove the container
down:
	@docker compose down

# Rebuild the image from scratch (no cache) and start the container
rebuild:
	docker compose build --no-cache
	@docker compose up -d
	@echo ""
	@echo "Jupyter Lab is running at:"
	@echo ""
	@echo "  http://localhost:8888"
	@echo ""
