.PHONY: setup config start restart stop status logs build clean happy help

export PATH := /opt/homebrew/bin:$(PATH)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Run one-click setup script
	./setup.sh

config: ## Generate runtime config from providers.toml
	python3 generate_config.py

start: ## Start all services via pm2
	pm2 start ecosystem.config.js

restart: ## Restart all services via pm2
	pm2 restart all

stop: ## Stop all services via pm2
	pm2 stop all

status: ## Show pm2 process status
	pm2 status

logs: ## Tail pm2 logs
	pm2 logs

build: ## Build cliproxy binary from submodule source
	cd source_code && go build -o ../cliproxy ./cmd/server/

happy: ## Configure Happy CLI to use cliproxyapi gateway
	./scripts/setup_happy_profile.sh

clean: ## Remove generated config files
	rm -f ecosystem.config.js lb.js
	rm -rf instances/*.yaml
