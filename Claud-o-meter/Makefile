.PHONY: build app dmg clean run

BINARY_NAME = Claud-o-meter
APP_NAME = Claud-o-meter.app
BUILD_DIR = .build/release
APP_DIR = build/$(APP_NAME)

build:
	swift build -c release

app: build
	@echo "Creating $(APP_NAME)..."
	@mkdir -p "$(APP_DIR)/Contents/MacOS"
	@mkdir -p "$(APP_DIR)/Contents/Resources"
	@cp "$(BUILD_DIR)/$(BINARY_NAME)" "$(APP_DIR)/Contents/MacOS/$(BINARY_NAME)"
	@cp Resources/Info.plist "$(APP_DIR)/Contents/Info.plist"
	@echo "APPLClau" > "$(APP_DIR)/Contents/PkgInfo"
	@echo "✅ Built $(APP_DIR)"

dmg: app
	@bash DMG/create-dmg.sh

run: build
	"$(BUILD_DIR)/$(BINARY_NAME)"

clean:
	swift package clean
	rm -rf build/
