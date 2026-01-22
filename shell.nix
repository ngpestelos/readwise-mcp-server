{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    curl
  ];

  shellHook = ''
    # Check if Claude Code is installed natively
    if [ ! -f "$HOME/.local/bin/claude" ]; then
      echo "Installing Claude Code (native)..."
      curl -fsSL https://claude.ai/install.sh | bash
    fi

    # Add Claude to PATH if not already there
    export PATH="$HOME/.local/bin:$PATH"

    # Indicate we're in a nix-shell with Claude Code
    export PS1="\[\033[1;32m\][nix-shell:claude]\[\033[0m\] $PS1"

    echo "Claude Code environment activated. Run 'claude' to use the CLI."
  '';
}
