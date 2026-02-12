# Start the container, reusing cached image layers
up:
	docker compose up -d

# Stop and remove the container
down:
	docker compose down

# Rebuild the image from scratch (no cache) and start the container
rebuild:
	docker compose build --no-cache
	docker compose up -d
