# Permissions

<p align="center">
  <a href="../README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="installation.md">Installation</a> &nbsp;&middot;&nbsp;
  <a href="models.md">Models</a>
</p>

Agent mode allows a model to request typed tools. It grants no authority by itself. The permission policy decides
whether each request is blocked, shown for approval, or eligible for narrow automatic execution.

## Policy

| Mode | Model-requested actions |
| --- | --- |
| `ask` | Show every tool action for approval. This is the default. |
| `read-auto` | Run project reads and safe machine-status checks automatically; ask for everything else. |
| `deny` | Block tool actions. Direct slash commands entered by the user remain available. |

| Request | `ask` | `read-auto` | `deny` |
| --- | --- | --- | --- |
| Active-project read | Approval | Automatic | Blocked |
| Allowlisted machine status | Approval | Automatic | Blocked |
| Named-path search outside the project | Approval | Approval | Blocked |
| External-path read | Approval | Approval | Blocked |
| Shell, patch, or package action | Approval | Approval | Blocked |
| Web search or page read | Approval | Approval | Blocked |

> Dairack is an approval boundary, not an operating-system sandbox. An approved shell command runs with the invoking
> user's privileges and network access.

## Approval

Approval is scoped to the exact displayed request. A shell approval authorizes that command once; it does not grant a
model unrestricted terminal access. Actions show their type, target, authority, result, exit status, and elapsed time.

Commands that require direct terminal input temporarily use the attached terminal, keeping operating-system password
prompts outside the Dairack composer. Non-interactive runtimes block those commands. `sudo -n` remains available when
credentials are already cached.

Text-only Coordinator consultation is bounded model inference rather than a client tool action, so it remains available
under `deny`. Image handoffs read a client file and therefore pass normal path and permission checks before inference.

## Cancellation

<kbd>Esc</kbd> appears as a stop control only when the active operation has a real cancellation path. Model inference,
shell process trees, project indexing and search, and streamed network reads are cancellable. Patch application and
short atomic maintenance operations finish with `FINISHING SAFELY` instead of offering a stop control that cannot work.

## Patches and Checkpoints

Patch requests display additions and removals, validate every target against the active project, perform a dry run, and
create a checkpoint before application. Checkpoints aid recovery; they are not a substitute for version control.

## Network Access

Search terms, URLs, retrieved pages, and any local details included in them leave the machine. Model-requested web
search and page reads always require approval, including under `read-auto`.

Dairack validates public HTTP and HTTPS destinations, revalidates redirects, applies time and size limits, and extracts
readable text without executing page scripts. Those controls reduce exposure but do not make untrusted content safe.

## Read-Auto Boundary

Automatic machine-status commands are parsed into a command-specific argument allowlist and revalidated immediately
before execution. They do not run through a shell. Unsupported options, file arguments, composed commands, or unknown
executable paths fall back to explicit approval.

Project reads are limited to the active workspace and structured Dairack tools. Arbitrary shell-based reads and paths
outside that workspace remain approval-gated. See the full [Security Policy](../SECURITY.md) for trust and disclosure
guidance.
