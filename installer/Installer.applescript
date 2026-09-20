on run
    set supportURL to "https://github.com/dneethling/claud-o-meter#install-one-command"
    display dialog "Set up Claud-o-meter for SwiftBar?" & return & return & "Setup installs the widget and its dependencies in your home folder, keeps your existing SwiftBar widgets, and connects to your signed-in Claude app or browser." buttons {"Cancel", "Continue"} default button "Continue" with title "Claud-o-meter Setup"
    try
        do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin command -v brew"
    on error
        display dialog "Homebrew is needed to install SwiftBar and Python. Install Homebrew first, then reopen this installer." buttons {"Cancel", "Open Homebrew"} default button "Open Homebrew" with title "One prerequisite"
        open location "https://brew.sh"
        return
    end try
    display dialog "Sign in to the Claude desktop app, or claude.ai in Chrome, Arc or Brave, before continuing." & return & return & "Setup may ask for Keychain access to read that session. It can take several minutes; you do not need to enter Terminal commands." buttons {"Cancel", "Install"} default button "Install" with title "Ready to install"
    set resourcePath to POSIX path of (path to resource "install.sh")
    set logFolder to (POSIX path of (path to library folder from user domain)) & "Logs/Claud-o-meter"
    do shell script "/bin/mkdir -p " & quoted form of logFolder
    set logPath to logFolder & "/installer.log"
    set progress total steps to -1
    set progress description to "Installing Claud-o-meter"
    set progress additional description to "Setting up SwiftBar, Python and your Claude connection…"
    try
        with timeout of 1800 seconds
            do shell script "/bin/bash " & quoted form of resourcePath & " > " & quoted form of logPath & " 2>&1"
        end timeout
        set progress total steps to 0
        display dialog "Setup finished. Look for the gauge in the top-right menu bar." & return & return & "If it says Re-auth, sign in to Claude and choose Refresh now. Display settings lets you select the compact view and colours." buttons {"Done"} default button "Done" with title "Claud-o-meter is ready"
    on error messageText number errorNumber
        set progress total steps to 0
        if errorNumber is -128 then return
        set choice to button returned of (display dialog "Setup could not finish. Your configuration has been kept. Open the setup log for the specific step that needs attention, then reopen this installer to retry." buttons {"Help", "Open log", "Close"} default button "Open log" with title "Setup needs attention")
        if choice is "Open log" then do shell script "/usr/bin/open -t " & quoted form of logPath
        if choice is "Help" then open location supportURL
    end try
end run
