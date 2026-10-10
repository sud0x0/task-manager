# tm

A minimal CLI task manager. Goals live in one markdown file and each task is
its own markdown file. The files are the source of truth, so edit them in editor
whenever that's quicker than the CLI.

Standard library only, no dependencies. Written for Python 3.9+.

## Install

```sh
ln -s "$PWD/bin/tm" ~/.local/bin/tm   # ~/.local/bin must be on your PATH
tm init                                # creates ~/tasks
```

Set `TM_HOME` to keep the files somewhere other than `~/tasks`.

## File Structure

```
~/tasks/
  goals.md                              all goals
  tasks/
    0001-rewrite-the-deploy-script.md   one file per task; 0001 is its id
```

A task:

```markdown
---
id: 1
title: Rewrite the deploy script
status: todo
priority: P1
goal: ship-v1
delegated_to: @alice
start: 2026-10-10
due: 2026-10-17
done_at:
---

Description goes here.

## Sub-todos

- [x] Audit the current script @bob
- [ ] Write the replacement

## Comments

### 2026-10-10 14:02

Blocked on ops access.
```

- `status`: `todo`, `doing`, `blocked`, `done` or `cancelled`
- `priority`: `P1` (highest), `P2` (default) or `P3`
- `goal`: a slug from `goals.md`
- dates are `YYYY-MM-DD`; `done_at` is filled in by `tm done`
- **Sub-todos must be checkboxes:** `- [ ] open`, `- [x] done`, `- [ ] task @who`.
  Indent two spaces to nest. A plain `- task` bullet is not a sub-todo.

A goal in `goals.md`:

```markdown
## Ship v1 of the task manager

slug: ship-v1
status: active

Description goes here.
```

Goal `status` is `active`, `achieved` or `dropped`.

**Hand edits are safe.** When `tm` rewrites a file it keeps everything you
added: extra keys, extra `##` sections, notes between sub-todos. If it can't
fully parse a file, it refuses to rewrite it and tells you which line to fix.

## Commands

```sh
tm add "Rewrite the deploy script" -p 1 -g ship-v1 --due +7d
tm add                          # no title: write the task in $EDITOR

tm list                         # open tasks, highest priority first
tm list --all                   # include done and cancelled
tm list -g ship-v1 -p P1        # filter by goal, priority, -s status, -d @who
tm list --overdue               # also --today, --sort due|id|title|goal

tm show 1
tm edit 1                       # open the file in $EDITOR
tm comment 1 "Blocked on ops access."
tm done 1
tm status 1 blocked             # task number, then the new status

tm goal add "Ship v1" --slug ship-v1
tm goal list
tm goal show ship-v1
tm goal set-status ship-v1 achieved

tm check                        # validate every file
```

Dates can be `2026-10-17`, `today`, `tomorrow`, `+3d`, `+2w` or a weekday
like `fri`. Every command takes `-h`, and `--root PATH` overrides `TM_HOME`.

## Good to know

- Writes are atomic: an interrupted write never leaves half a file.
- `tm edit` never throws away what you typed. If the saved file is invalid, it
  says what's wrong and leaves the file as you saved it.
- If `tm add` can't use what you wrote, your draft is kept and its path printed.
- Ids are never reused.
- `$TM_EDITOR`, then `$VISUAL`, then `$EDITOR`, then `vim`.

## Tests

```sh
python3 -m unittest discover -s tests -t . -v
```
