#!/bin/bash
# template-replace.sh - Replace a {{template-var}} token in a file with piped text.
#
# Usage:   echo '{{var-name}} replacement text' | ./template-replace.sh <template-file>
#
# The replacement text may span multiple lines and contain any characters
# (| & / " \ etc.) - replacement is literal, not regex-based.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <template-file>" >&2
    echo "Then pipe: {{template-var}} <replacement text>" >&2
    echo "Example: echo '{{name}} Hello World' | $0 myfile.txt" >&2
    exit 1
fi

TEMPLATE_FILE="$1"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: File '$TEMPLATE_FILE' not found" >&2
    exit 1
fi

INPUT="$(cat)"

if [ -z "$INPUT" ]; then
    echo "Error: No input provided via pipe" >&2
    exit 1
fi

# Input must begin with a {{...}} token on its first line.
FIRST_LINE="${INPUT%%$'\n'*}"
case "$FIRST_LINE" in
    '{{'*'}}'*) ;;
    *)
        echo "Error: Input must start with {{template-var}}" >&2
        echo "Format: {{var-name}} replacement text" >&2
        exit 1
        ;;
esac

# Split the leading {{...}} token from the replacement that follows one space.
REST="${INPUT#\{\{}"        # drop leading {{
VAR_NAME="${REST%%\}\}*}"   # text before the first }}
TOKEN="{{${VAR_NAME}}}"     # full literal token, e.g. {{GOAL}}
AFTER="${REST#*\}\}}"       # everything after the first }}
REPLACEMENT="${AFTER# }"    # drop exactly one separating space

if [ -z "$VAR_NAME" ]; then
    echo "Error: Could not parse template variable from input" >&2
    exit 1
fi

# Fail fast if the token is not actually present (instead of silently no-op'ing).
if ! grep -qF -- "$TOKEN" "$TEMPLATE_FILE"; then
    echo "Error: Token '$TOKEN' not found in '$TEMPLATE_FILE'" >&2
    exit 1
fi

TEMP_FILE="$(mktemp)"

# Literal (non-regex) replacement via awk: handles multi-line values and any
# special characters without delimiter or escaping problems. Values are passed
# through the environment so newlines survive intact.
TOKEN="$TOKEN" REPLACEMENT="$REPLACEMENT" awk '
    BEGIN { tok = ENVIRON["TOKEN"]; rep = ENVIRON["REPLACEMENT"]; tl = length(tok) }
    {
        line = $0
        out = ""
        while ((p = index(line, tok)) > 0) {
            out = out substr(line, 1, p - 1) rep
            line = substr(line, p + tl)
        }
        print out line
    }
' "$TEMPLATE_FILE" > "$TEMP_FILE"

mv "$TEMP_FILE" "$TEMPLATE_FILE"

echo "Replaced $TOKEN"
