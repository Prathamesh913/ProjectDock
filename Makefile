PREFIX ?= $(HOME)/.local
BIN_DIR := $(PREFIX)/bin
REPO := $(shell dirname $(abspath $(lastword $(MAKEFILE_LIST))))
BIN := $(REPO)/bin/projectdock
LINK := $(BIN_DIR)/projectdock

# Global Hyprland shortcut used to summon the launcher. Override with, e.g.:
#   make install SHORTCUT="SUPER + K"
# SUPER+D is the default because Omarchy's defaults claim several close
# neighbours (notably SUPER+P = Pseudo window tiling).
SHORTCUT ?= SUPER + D
HYPR_BINDINGS := $(HOME)/.config/hypr/bindings.lua

.PHONY: install uninstall test run

install:
	@mkdir -p $(BIN_DIR)
	@ln -sf $(BIN) $(LINK)
	@echo "Installed $(LINK) -> $(BIN)"
	@if grep -q 'o\.bind(.*"ProjectDock"' $(HYPR_BINDINGS) 2>/dev/null; then \
		echo "ProjectDock shortcut already present in $(HYPR_BINDINGS) (left untouched)"; \
	else \
		printf '\n-- >>> ProjectDock >>> --\n-- ProjectDock: fast, keyboard-first project launcher.\n-- Default shortcut: SUPER+D. SUPER+P is taken by Omarchy Pseudo\n-- tiling (default/hypr/bindings/tiling.lua); do not reuse it.\n-- Change it here, or reinstall with: make install SHORTCUT="SUPER + K"\no.bind("$(SHORTCUT)", "ProjectDock", "$(BIN) toggle")\n-- <<< ProjectDock <<< --\n' >> $(HYPR_BINDINGS); \
		echo "Added $(SHORTCUT) -> ProjectDock to $(HYPR_BINDINGS)"; \
	fi
	@echo "Reload Hyprland if it is running: hyprctl reload"

uninstall:
	@rm -f $(LINK)
	@echo "Removed $(LINK)"
	@if [ -f $(HYPR_BINDINGS) ]; then \
		sed -i '\|^-- >>> ProjectDock >>> --$|,\|^-- <<< ProjectDock <<< --$|d' $(HYPR_BINDINGS); \
		echo "Removed ProjectDock bindings from $(HYPR_BINDINGS)"; \
	fi

test:
	python3 -m unittest discover -s tests

run:
	$(BIN) toggle
