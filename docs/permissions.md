# Permissions

Agent mode lets a model request typed tools. It does not grant an action by itself. The permission policy decides
whether the request is blocked, shown for approval, or eligible for narrow automatic execution.

| Mode | Behavior |
| --- | --- |
| `ask` | Prompt for every model-requested tool action. This is the default. |
| `read-auto` | Auto-run project reads and safe status commands. Ask for writes, shell, external paths, and network. |
| `deny` | Block model-requested tools. Direct slash commands entered by the user remain available. |

Coordinator-internal text-only specialist consultation is bounded model inference rather than a client tool action,
so it remains available in `deny` mode and does not require approval. Image handoffs read the file on the client and
therefore follow path and permission checks before inference.

Approval is per request. A shell approval authorizes exactly the displayed command, but the command runs through the
user's shell and has that user's OS privileges. The default TUI suspends its composer and opens an attached terminal
for commands that need direct TTY input, so password prompts cannot overlap the Dairack input field. Non-interactive
runtimes block those commands; `sudo -n` remains available when credentials are already cached.

While work is active, `Esc` is offered only when the implementation has a real cancellation path. Shell process trees,
project indexing and search, model inference, and streamed network reads are cancellable. Patch application and short
atomic maintenance operations finish without a misleading stop affordance.

Patch requests display additions and removals, perform a dry run, validate target paths, and create a checkpoint before
application. Checkpoints are recovery aids, not a substitute for version control.

Web search terms and fetched URLs leave the machine. They always require approval when initiated by a model, including
under `read-auto`, because query strings can carry local information.

Automatic machine-status commands are parsed into a command-specific argv allowlist and revalidated immediately before
execution. They do not run through a shell. Any unsupported option, file argument, command composition, or executable
path falls back to explicit approval.
