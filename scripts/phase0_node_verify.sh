#!/usr/bin/env bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
echo "nvm: $(nvm --version)"
echo "node: $(node --version)"
echo "npm: $(npm --version)"
nvm use 24 >/dev/null 2>&1 && echo "nvm use 24: OK" || echo "nvm use 24: FAIL"
