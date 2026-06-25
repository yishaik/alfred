---
type: Module
title: expenses.py — pocket expense tracker
description: A pocket expense ledger ("amount #category note") with monthly, per-category summaries.
resource: tgbridge/expenses.py
tags: [module, mini-app]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
A lightweight expense tracker. Pure parsing and ledger logic.

# Key behaviors
- Parses `amount #category note`.
- Tracks by month; breaks down by category.

# Collaborators
Persisted by [manager](/components/manager.md). Sibling mini-apps:
[todos](/components/mini-apps/todos.md), [contacts](/components/mini-apps/contacts.md).
