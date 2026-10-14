PUT YOUR PDF FILES IN THIS FOLDER
=================================

Copy engineering PDFs (manuals, datasheets, procedures) into this folder.
Sub-folders are fine. The assistant checks this folder every 15 seconds and
indexes new or changed PDFs automatically. In the assistant, open
"Document library" to see each file's status, or press "Check folder now".

- Replacing a PDF with a new version re-indexes it and drops the old copy.
- Deleting a PDF here does NOT remove it from the assistant unless
  EKA_INBOX_REMOVE_DELETED=1 is set in config.env.
- Scanned PDFs need Tesseract OCR installed (see START_HERE.md).
- Videos and other file types are listed but not indexed yet (phase 2).
