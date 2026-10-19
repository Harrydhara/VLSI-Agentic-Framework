# Custom strategies

Strategies saved from the dashboard's **5. Strategy editor** land here, one folder each
(`prompts/custom/<name>/`). They work like any other strategy folder: pick them in the
dashboard or pass them to `vlsi-agent run --prompts` / `vlsi-agent bench --prompts`.

Docker Compose mounts this folder into the app container, so strategies saved there
appear here too.
