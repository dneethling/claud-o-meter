PROJECT := ClaudOMeterNotch.xcodeproj
SCHEME := ClaudOMeterNotch
DEST := platform=macOS
APP := $(CURDIR)/build/Build/Products/Debug/ClaudOMeterNotch.app
INSTALL_DIR := $(HOME)/Applications

.PHONY: gen build test run demo install

gen:
	xcodegen generate

build: gen
	xcodebuild -project $(PROJECT) -scheme $(SCHEME) -destination '$(DEST)' -derivedDataPath build -configuration Debug build

test: gen
	xcodebuild -project $(PROJECT) -scheme $(SCHEME) -destination '$(DEST)' -derivedDataPath build -configuration Debug test

run: build
	open "$(APP)"

demo: build
	CODENOTCH_DEMO=1 "$(APP)/Contents/MacOS/ClaudOMeterNotch"

install: build
	@mkdir -p "$(INSTALL_DIR)"
	@test ! -e "$(INSTALL_DIR)/ClaudOMeterNotch.app" || (echo "Quit and move the existing comparison app before reinstalling."; exit 1)
	ditto "$(APP)" "$(INSTALL_DIR)/ClaudOMeterNotch.app"
	open "$(INSTALL_DIR)/ClaudOMeterNotch.app"
