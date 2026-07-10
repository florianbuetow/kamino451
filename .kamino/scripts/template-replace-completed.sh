#!/bin/bash
# template-replace-completed.sh - Exit 1 if any {{template-var}} remain in the file.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <template-file>" >&2
    exit 1
fi

TEMPLATE_FILE="$1"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: File '$TEMPLATE_FILE' not found" >&2
    exit 1
fi

# Match any {{...}} token. Character classes ([{] [}]) avoid the BRE/ERE
# brace-interval pitfalls that differ between GNU and BSD grep.
if matches="$(grep -nE '[{][{][^}]*[}][}]' "$TEMPLATE_FILE")"; then
    echo "Error: Template variables still present in '$TEMPLATE_FILE'" >&2
    echo "Found:" >&2
    printf '%s\n' "$matches" | sed 's/^/  /' >&2
    exit 1
fi

echo "✓ No template variables found in '$TEMPLATE_FILE'"
exit 0
