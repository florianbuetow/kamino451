#!/bin/bash
# list-library-agents.sh - List all agents in the library tier with their name and description.
#
# Usage: bash .kamino/scripts/list-library-agents.sh
#
# Scans .kamino/agents/library/ recursively for .md files, extracts the
# agent_name, agent_description, required_inputs, and hardcoded_properties
# fields from the YAML frontmatter, and prints one record per agent to stdout.
#
# Output format (one block per agent, separated by ---------):
#   path: <path relative to library/>
#   name: <agent_name>
#   description: <agent_description>
#   required_inputs: {{VAR1}}, {{VAR2}}, ...
#   hardcoded_properties: PROP1, PROP2, ...

set -euo pipefail

LIBRARY_DIR=".kamino/agents/library"

if [ ! -d "$LIBRARY_DIR" ]; then
    echo "Error: library directory not found: $LIBRARY_DIR" >&2
    echo "Run this script from the project root." >&2
    exit 1
fi

found=0

while IFS= read -r filepath; do
    relpath="${filepath#"$LIBRARY_DIR"/}"

    # Extract agent_name from YAML frontmatter (between first pair of --- lines)
    name=$(awk '
        /^---/ { fm++; next }
        fm == 1 && /^agent_name:/ {
            sub(/^agent_name:[[:space:]]*/, "")
            gsub(/^[[:space:]]*"|"[[:space:]]*$/, "")
            print; exit
        }
    ' "$filepath")

    # Extract agent_description from YAML frontmatter
    desc=$(awk '
        /^---/ { fm++; next }
        fm == 1 && /^agent_description:/ {
            sub(/^agent_description:[[:space:]]*/, "")
            gsub(/^[[:space:]]*"|"[[:space:]]*$/, "")
            print; exit
        }
    ' "$filepath")

    # Extract required_inputs ({{template variables}} the caller must fill)
    required_inputs=$(awk '
        /^---/ { fm++; next }
        fm == 1 && /^required_inputs:/ {
            sub(/^required_inputs:[[:space:]]*/, "")
            gsub(/[\[\]]/, "")
            n = split($0, a, /,[[:space:]]*/);
            result = ""
            for (i = 1; i <= n; i++) {
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", a[i])
                if (a[i] == "") continue
                if (result != "") result = result ", "
                result = result "{{" a[i] "}}"
            }
            print result; exit
        }
    ' "$filepath")

    # Extract hardcoded_properties (baked sections; plain names, not variables)
    hardcoded=$(awk '
        /^---/ { fm++; next }
        fm == 1 && /^hardcoded_properties:/ {
            sub(/^hardcoded_properties:[[:space:]]*/, "")
            gsub(/[\[\]]/, "")
            n = split($0, a, /,[[:space:]]*/);
            result = ""
            for (i = 1; i <= n; i++) {
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", a[i])
                if (a[i] == "") continue
                if (result != "") result = result ", "
                result = result a[i]
            }
            print result; exit
        }
    ' "$filepath")

    if [ -z "$name" ] && [ -z "$desc" ]; then
        continue
    fi

    [ "$found" -gt 0 ] && echo "---------"
    printf "path: %s\nname: %s\ndescription: %s\nrequired_inputs: %s\nhardcoded_properties: %s\n" "$relpath" "$name" "$desc" "$required_inputs" "$hardcoded"
    found=$((found + 1))

done < <(find "$LIBRARY_DIR" -name "*.md" | sort)

if [ "$found" -eq 0 ]; then
    echo "No agents found in $LIBRARY_DIR" >&2
    exit 1
fi
