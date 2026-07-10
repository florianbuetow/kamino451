#!/bin/bash
# template-variable-checks.sh - Verify that an agent .md file's frontmatter and
# its body agree. Enforces the agent-surface contract:
#
#   Every agent declares two lists in its YAML frontmatter:
#     required_inputs:      [ ... ]   # {{TEMPLATE_VARIABLES}} the caller fills
#     hardcoded_properties: [ ... ]   # <TAG> sections baked into the agent
#
#   Both keys are REQUIRED. The following invariants are checked:
#
#   C1. required_inputs <-> body {{VARIABLES}}, in BOTH directions:
#         - every name in required_inputs appears as a {{VAR}} token, and
#         - every {{VAR}} token in the body is declared in required_inputs.
#   C2. Both required_inputs and hardcoded_properties keys are present.
#   C3. Every name in hardcoded_properties is a genuinely baked section:
#         - a <NAME> ... </NAME> section exists and is well-formed, and
#         - its content is NOT a bare {{NAME}} passthrough (that would be an
#           input, not a baked property).
#   C4. required_inputs and hardcoded_properties are disjoint (no name in both).
#
#   NOT checked (deliberately, to stay robust against the agents' free-form
#   structural sections such as <RULES>/<STEPS>): completeness of
#   hardcoded_properties. A baked section that is left undeclared is not flagged;
#   only the names that ARE declared are verified against the body.
#
# The single argument may be EITHER:
#   - an agent file  -> that one file is checked, or
#   - a directory    -> it is searched recursively for *.md files and every
#                       agent file inside is checked.
#
# A file is treated as an agent iff it declares `required_inputs:`. Files with no
# `required_inputs:` (e.g. index.md) are skipped, not failed. An un-filled
# blueprint scaffold (a declared list still contains a <<...>> placeholder) is
# also skipped. A file that still uses the deprecated `inputs:` /
# `input_parameters:` key is FAILED, so un-migrated agents cannot pass silently.
#
# Exits 0 only if every checked agent matches. Exits 1 on any mismatch, on a
# missing path, or when a single explicitly-named file is not an agent.
#
# Usage:   ./template-variable-checks.sh <agent-file.md | agents-directory>

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <agent-file.md | agents-directory>" >&2
    exit 1
fi

TARGET="$1"

# Extract the bracketed list value of a frontmatter key into a comma string.
# Echoes nothing if the key is absent or not a [ ... ] list.
list_value() {
    local file="$1" key="$2" line
    line="$(grep -E "^${key}:" "$file" | head -1 || true)"
    [ -z "$line" ] && return 0
    case "$line" in
        *'['*']'*) ;;
        *) return 0 ;;
    esac
    line="${line#*[}"
    printf '%s' "${line%]*}"
}

# Emit each trimmed, non-empty element of a comma-separated list on its own line.
# (bash 3.2 compatible: no namerefs, no empty-array expansion under `set -u`.)
clean_list() {
    local raw="$1" parts i name
    local IFS=','
    read -ra parts <<< "$raw"
    for ((i = 0; i < ${#parts[@]}; i++)); do
        name="$(printf '%s' "${parts[i]}" | tr -d '[:space:]')"
        [ -n "$name" ] && printf '%s\n' "$name"
    done
}

# check_file <file>
#   prints a one-line result; returns: 0 = pass, 1 = mismatch, 2 = not an agent
check_file() {
    local file="$1"

    # A file is an agent iff it declares required_inputs:.
    if ! grep -qE '^required_inputs:' "$file"; then
        # Deprecated key still in use -> hard failure, never a silent skip.
        if grep -qE '^(inputs|input_parameters):' "$file"; then
            echo "✗ $file" >&2
            echo "    uses deprecated 'inputs:'/'input_parameters:' — rename to 'required_inputs:'" >&2
            return 1
        fi
        return 2
    fi

    local ri_raw hp_raw
    ri_raw="$(list_value "$file" required_inputs)"
    if ! grep -qE '^required_inputs:[[:space:]]*\[' "$file"; then
        echo "✗ $file" >&2
        echo "    'required_inputs:' must be a [ ... ] list" >&2
        return 1
    fi

    # Un-filled blueprint scaffold: a declared list still has a <<...>> placeholder.
    case "$ri_raw" in *'<<'*) return 2 ;; esac

    # C2: hardcoded_properties key is mandatory.
    if ! grep -qE '^hardcoded_properties:' "$file"; then
        echo "✗ $file" >&2
        echo "    missing required 'hardcoded_properties:' declaration" >&2
        return 1
    fi
    if ! grep -qE '^hardcoded_properties:[[:space:]]*\[' "$file"; then
        echo "✗ $file" >&2
        echo "    'hardcoded_properties:' must be a [ ... ] list (use [] if none)" >&2
        return 1
    fi
    hp_raw="$(list_value "$file" hardcoded_properties)"
    case "$hp_raw" in *'<<'*) return 2 ;; esac

    local required=() hardcoded=() n
    while IFS= read -r n; do required+=("$n"); done < <(clean_list "$ri_raw")
    while IFS= read -r n; do hardcoded+=("$n"); done < <(clean_list "$hp_raw")

    local errors=()

    # C1: required_inputs <-> body {{VARIABLES}}, both directions.
    local body_vars var used found _i _j
    body_vars="$(grep -oE '\{\{[^}]*\}\}' "$file" \
        | sed -E 's/^\{\{[[:space:]]*//; s/[[:space:]]*\}\}$//; s/[[:space:]]*\|.*$//; s/[[:space:]]*$//' \
        | sort -u || true)"

    for ((_i = 0; _i < ${#required[@]}; _i++)); do
        var="${required[_i]}"
        if ! grep -qE "\{\{[[:space:]]*${var}[[:space:]]*(\}\}|\|)" "$file"; then
            errors+=("required_input '${var}' is declared but never used as {{${var}}} in the body")
        fi
    done
    while IFS= read -r used; do
        [ -z "$used" ] && continue
        found=0
        for ((_j = 0; _j < ${#required[@]}; _j++)); do
            [ "${required[_j]}" = "$used" ] && { found=1; break; }
        done
        [ "$found" -eq 0 ] && errors+=("{{${used}}} is used in the body but not declared in required_inputs")
    done <<< "$body_vars"

    # C4: required_inputs and hardcoded_properties must be disjoint.
    for ((_i = 0; _i < ${#hardcoded[@]}; _i++)); do
        for ((_j = 0; _j < ${#required[@]}; _j++)); do
            [ "${hardcoded[_i]}" = "${required[_j]}" ] && \
                errors+=("'${hardcoded[_i]}' is in both required_inputs and hardcoded_properties — it must be exactly one")
        done
    done

    # C3: every hardcoded property is a genuinely baked, well-formed section.
    # `nospace` is the whole file with all whitespace removed, so a bare
    # passthrough section reads as the contiguous "<TAG>{{TAG}}</TAG>" regardless
    # of how the section was wrapped across lines.
    local name nospace
    nospace="$(tr -d '[:space:]' < "$file")"
    for ((_i = 0; _i < ${#hardcoded[@]}; _i++)); do
        name="${hardcoded[_i]}"
        if ! grep -qE "<${name}>" "$file"; then
            errors+=("hardcoded_property '${name}' has no <${name}> section in the body")
            continue
        fi
        if ! grep -qE "</${name}>" "$file"; then
            errors+=("<${name}> section is missing its closing </${name}> tag")
            continue
        fi
        case "$nospace" in
            *"<${name}>{{${name}}}</${name}>"*)
                errors+=("<${name}> is a bare {{${name}}} passthrough, not baked content — declare it in required_inputs, not hardcoded_properties")
                ;;
        esac
    done

    if [ "${#errors[@]}" -eq 0 ]; then
        echo "✓ $file (${#required[@]} required_inputs, ${#hardcoded[@]} hardcoded_properties)"
        return 0
    fi

    echo "✗ $file" >&2
    for ((_i = 0; _i < ${#errors[@]}; _i++)); do
        echo "    ${errors[_i]}" >&2
    done
    return 1
}

if [ -f "$TARGET" ]; then
    rc=0
    check_file "$TARGET" || rc=$?
    if [ "$rc" -eq 2 ]; then
        echo "Error: '$TARGET' is not an agent file (no required_inputs declaration)" >&2
        exit 1
    fi
    exit "$rc"
elif [ -d "$TARGET" ]; then
    pass=0 fail=0 skip=0 status=0
    while IFS= read -r f; do
        rc=0
        check_file "$f" || rc=$?
        case "$rc" in
            0) pass=$((pass + 1)) ;;
            1) fail=$((fail + 1)); status=1 ;;
            2) skip=$((skip + 1)); echo "– skipped (not an agent): $f" ;;
        esac
    done < <(find "$TARGET" -type f -name '*.md' | sort)

    echo ""
    echo "Checked $((pass + fail)) agent file(s): $pass passed, $fail failed, $skip skipped."
    exit "$status"
else
    echo "Error: '$TARGET' not found" >&2
    exit 1
fi
