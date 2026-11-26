---
name: linear-tickets
description: Use when creating, rewriting or splitting a Linear ticket, or when a Linear save was refused by the ticket check. Covers when to file one, the short human part on top, the Agent notes block below, and keeping one fact per ticket.
---

A ticket has two readers. A person scans the top in ten seconds. An agent picking up the work later needs every detail. Write the top for the person and put the rest in Agent notes.

The Linear connector's own instructions already say how to write the `+++ Agent notes` block. This skill adds what goes above it and when to file at all.

## Before you file

- **File only with a fix.** Open a ticket when a fix PR exists or should be written. A finding nobody can act on goes as a comment on the ticket for its class of problem.
- **Check the cause first.** Confirm the root cause on the default branch. A ticket that sends a reviewer after a bug that doesn't exist costs more than no ticket.
- **Draft in chat first.** Show the draft and save it to Linear only when the user says so.

## The top part

At most 15 lines, plain words, no code. The ticket check refuses more.

Write it BLUF and concise. The bold first line is the bottom line: a reader who stops there knows what changes and why it matters. Every line after it adds one fact.

```
**<The outcome in one line: what is true when this is done.>**

Why: <one or two sentences: what breaks or who waits today.>

What: <the change, in a sentence or a small table.>

Done when: <a check a person can run or see.>

+++ Agent notes
<object names, SQL, file paths, logs, backfill steps, verification queries>
+++
```

- No "Out of scope" section. Say only what matters.
- Names of tables, tags, files and services go in Agent notes, unless the person needs one to understand the ticket.

## One fact, one ticket

- A parent holds a short summary and links. Findings live on the subtask that owns them.
- After rewriting a parent or a subtask, compare them and cut what repeats.

## Correct in place

A wrong root cause or stale numbers get fixed in the title and description, not in a comment. Comments are for why a ticket was canceled, with the evidence.
