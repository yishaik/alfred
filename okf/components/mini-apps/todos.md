---
type: Module
title: todos.py — kanban to-dos
description: A small per-user kanban (todo / doing / done) mini-app.
resource: tgbridge/todos.py
tags: [module, mini-app]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
A curated, single-list-per-user to-do board. Pure logic; the
[manager](/components/manager.md) persists it.

# Key behaviors
- Three columns: todo, doing, done.
- Items move between columns; list kept intentionally small.

# Collaborators
Persisted by [manager](/components/manager.md). Sibling mini-apps:
[expenses](/components/mini-apps/expenses.md), [contacts](/components/mini-apps/contacts.md).
