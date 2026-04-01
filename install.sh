#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "🐾 Installing Buddy — Claude Code Virtual Pet"
echo "   Directory: $SCRIPT_DIR"
echo ""

# Check Python 3
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        if [ "$major" -ge 3 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Python 3 not found. Install it with: brew install python@3.13"
    exit 1
fi
echo "✓ Found Python: $($PYTHON --version)"

# Create venv if needed
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv "$SCRIPT_DIR/venv"
fi

# Install dependencies
echo "  Installing PyObjC..."
"$SCRIPT_DIR/venv/bin/pip" install -q pyobjc-framework-Cocoa pyobjc-framework-Quartz 2>&1 | tail -1
echo "✓ Dependencies installed"

# Find the Python.app framework binary (required for macOS GUI)
PYTHON_APP=""
FRAMEWORK_BASE="$("$SCRIPT_DIR/venv/bin/python3" -c "import sys; print(sys.base_prefix)")"
CANDIDATE="$FRAMEWORK_BASE/Resources/Python.app/Contents/MacOS/Python"
if [ -f "$CANDIDATE" ]; then
    PYTHON_APP="$CANDIDATE"
else
    # Try Homebrew locations
    for p in /opt/homebrew/Cellar/python@*/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python \
             /usr/local/Cellar/python@*/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python; do
        if [ -f "$p" ]; then
            PYTHON_APP="$p"
            break
        fi
    done
fi

if [ -z "$PYTHON_APP" ]; then
    echo "⚠ Could not find Python.app framework binary. Falling back to venv python."
    echo "  (The app may not display windows properly — see README)"
    PYTHON_APP="$SCRIPT_DIR/venv/bin/python3"
fi
echo "✓ GUI Python: $PYTHON_APP"

# Create/update the .app bundle
APP_DIR="$SCRIPT_DIR/Buddy.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Buddy</string>
    <key>CFBundleIdentifier</key>
    <string>com.claude.buddy</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>buddy</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <false/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>CFBundleIconFile</key>
    <string>Buddy</string>
</dict>
</plist>
PLIST

# Copy icon
if [ -f "$SCRIPT_DIR/Buddy.icns" ]; then
    cp "$SCRIPT_DIR/Buddy.icns" "$APP_DIR/Contents/Resources/Buddy.icns"
fi

cat > "$APP_DIR/Contents/MacOS/buddy" << LAUNCHER
#!/bin/bash
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/venv/lib/python\$("$SCRIPT_DIR/venv/bin/python3" -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')")/site-packages"
exec "$PYTHON_APP" buddy.py
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/buddy"

echo "✓ Buddy.app created"
echo ""
echo "🎉 Done! Launch with:"
echo "   open $APP_DIR"
echo ""
echo "   Or double-click Buddy.app in Finder."
echo "   Buddy reads your Claude buddy from ~/.claude.json automatically."
