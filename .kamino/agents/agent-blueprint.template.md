---
agent_name: <<AGENT_NAME>>
agent_description: "<<AGENT_DESCRIPTION>>"
model: <<MODEL>>
effort: <<EFFORT>>
required_inputs: [<<REQUIRED_INPUTS>>]
hardcoded_properties: [<<HARDCODED_PROPERTIES>>]
version: 1
---
<<PERSONA>>

<GOAL>
{{GOAL}}
</GOAL>

<DEFINITION_OF_DONE>
All steps have been completed following the rules to reach the goal and the output was provided in the required output format.
</DEFINITION_OF_DONE>

Each input below may be provided either as the content itself or as a path to a file that contains the content. If an input value is a path to an existing file, read that file and use its contents; otherwise use the value as the content directly. `<OUTPUT_FILE>` is always a path to write to, never read as content.

<INPUTS>
<<INPUTS>>
</INPUTS>

<OUTPUT_FILE>
{{OUTPUT_FILE}}
</OUTPUT_FILE>

<RULES>
<<RULES>>
</RULES>

<STEPS>
<<STEPS>>
</STEPS>

<OUTPUT_FORMAT>
<<OUTPUT_FORMAT>>
</OUTPUT_FORMAT>
