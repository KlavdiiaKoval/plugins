# Diagrams (step 7)

Make every diagram readable in light and dark viewers (GitHub, IDE previews):

- First line of each block: `%%{init: {"themeVariables": {"fontFamily": "monospace"}, "flowchart": {"htmlLabels": false}}}%%`. Without it, IDE previews cut off labels.
- Package colors, always with dark text: `classDef new fill:#c8f7c5,stroke:#2e7d32,color:#111`, `classDef changed fill:#cfe3ff,stroke:#1f5fbf,color:#111`, `classDef same fill:#ffffff,stroke:#888,color:#111`.
- Subgraphs get `style <id> fill:none,stroke:#888`. A filled subgraph hides its title and the edges in one of the themes.
- State diagrams use `direction LR`.
- Keep edge labels to one or two words. No semicolons in labels.
- Render each block with `mmdc` twice, default and `-t dark -b '#1e1e1e'`, and look at both PNGs. Fix any cut-off label, overlap or invisible text before handing over.
