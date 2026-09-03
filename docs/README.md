# Client documentation

`Expense-Log-Bot-User-Guide.pdf` — the end-user guide. No setup, no commands
line, nothing technical: how to send an expense, what the replies mean, and
what to do when something fails.

The PDF is generated from `user-guide.html`, which is the file to edit:

```bash
google-chrome --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="docs/Expense-Log-Bot-User-Guide.pdf" \
  "file://$PWD/docs/user-guide.html"
```

A4, 7 pages. Every example in it is real output from the running bot, not
invented for the document — keep it that way when you edit.
