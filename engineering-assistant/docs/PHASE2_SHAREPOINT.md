# Phase 2 — SharePoint list (PDFs and videos)

Status: **design only, not implemented.** It can't be built or tested
without access to the tenant, so nothing here is claimed to work yet.
Phase 1 (the `pdfs/` folder) is implemented and tested.

## What is already in place

Phase 1 was built on a source interface so SharePoint plugs in without
changing indexing, search, answering or the UI:

| Piece | File | Role |
|---|---|---|
| `Source`, `SourceItem` | `eka/sources/base.py` | `list()` returns items with a stable key, a version and a kind (pdf/video/other); `fetch(item)` returns the bytes |
| `FolderSource` | `eka/sources/folder.py` | Phase 1 implementation |
| `SourceSync` | `eka/sources/sync.py` | Polls a source: indexes new items, re-indexes changed ones, keeps or removes deleted ones, records errors per item and never retries a failed item until it changes |
| `documents.source`, `source_version` | `eka/store.py` | Which item each document came from, and which version |
| `GET /api/sources`, `POST /api/sources/scan` | `eka/api.py` | Status in the library panel and "Check now" |

Phase 2 adds a `SharePointSource` (same interface) and registers it next to
the folder source in `eka/service.py`. Both can run at once.

## How it would connect

* **Microsoft Graph API**, read-only, through an Entra ID (Azure AD) app
  registration with the **`Sites.Selected`** permission, granted by IT for
  this one site only (not all of SharePoint). Authentication with a
  certificate (preferred) or a client secret, stored in an environment
  variable or the Windows credential store, never in `config.env` or the logs.
* List the items: `GET /sites/{site-id}/lists/{list-id}/items?expand=fields`,
  then use **delta queries** (`.../items/delta`) so each check only fetches
  what changed.
* For each item, find the file — either the item itself is a file in a
  document library (`driveItem`), the list has a link/lookup column, or the
  PDF is an attachment — and download it with `.../driveItem/content`.
* **Version** = the item's `eTag` / `lastModifiedDateTime`, so unchanged items
  are never downloaded again (same rule as the folder).
* **Metadata**: list columns (e.g. equipment model, document type, revision,
  site, discipline) are mapped to document fields, so they show in the
  library and can filter searches and clarification questions.
* Citations gain a link back to the SharePoint item, next to the page number.
* Permissions: phase 2 indexes what the app account can read. If different
  users may see different items, per-user filtering (security trimming) is a
  separate piece of work and must be decided before go-live.

## Videos

Videos can't be searched directly; their speech is turned into timestamped
text first. Options, in order of preference:

1. **Existing transcripts**: Stream/SharePoint videos often have a `.vtt`
   transcript already. Use it as-is (no extra processing).
2. **Local transcription** with Whisper (e.g. `faster-whisper`, small/medium
   model, 0.5–1.5 GB, runs on CPU; roughly real time to 3× real time per
   video on a normal PC). Nothing leaves the machine. This is a heavyweight
   dependency and would only be installed after agreement.
3. Index title, description and list columns only (no speech), as a fallback.

Answers from a video cite **video name + timestamp** (e.g. "Pump overhaul
video, 12:40") and the viewer opens the video at that moment, the same way
PDF citations open the page.

## What we need from you / IT

1. SharePoint **site URL** and **list name** (a screenshot of the list with its columns helps).
2. Where the PDFs and videos live: in the list itself, a document library, attachments, or links.
3. Which **columns** matter (equipment, model, revision, document type, …).
4. An **app registration** with `Sites.Selected` (read) on that site, or the
   authentication method IT prefers; tenant ID and client ID.
5. Whether all users may see all documents, or access must follow SharePoint permissions.
6. Approximate numbers: how many PDFs and videos, total size, typical video length,
   and whether transcripts already exist.
7. Network: can the PC that runs the assistant reach `graph.microsoft.com`
   (proxy settings if any)?

## Acceptance tests (to be written with the implementation)

* Mocked Graph responses (no tenant needed): new / changed / deleted items,
  paging, delta tokens, throttling (HTTP 429 with `Retry-After`), expired
  credentials, an item without a file, a non-PDF file, a large video.
* Against a test site once access is granted: a test list with synthetic
  PDFs and a short synthetic video.
