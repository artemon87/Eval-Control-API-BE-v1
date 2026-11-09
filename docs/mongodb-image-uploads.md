# MongoDB image uploads

Suggestions and What's New store one binary image per document in IMAGE_ATTACHMENTS_COLLECTION
(default image_attachments), using the existing application database. No cloud storage client,
GCP credentials, or bucket settings are needed. Parent records contain image metadata and a
storage_id; list endpoints do not fetch binary content. The MongoDB user needs read/write
access to this collection. Include it in database backup/restore and capacity planning.

Limits: up to three suggestion images; one product update screenshot; 3 MiB per file,
checked before and after HEIC-to-JPEG conversion. Existing 10 MiB environment overrides
must be removed or changed to 3145728 before deployment. Both max-byte settings are
capped at 3 MiB. PNG/JPEG/HEIC/HEIF validation remains; invalid or oversized content
returns a clear error. This change does not automatically resize files.

Uploads remain multipart submissions with the parent form. Rollback removes earlier
images if a later file or parent write fails. Cleanup failures are logged for operator
follow-up; process termination or an ambiguous database write can still leave orphans.
This change does not implement retry idempotency or a scheduled orphan collector.

Image GET routes require authentication, verify the parent record's image reference, and
return bytes through the existing application URLs. Missing and legacy GCP references
return 404 before response headers are sent. Images are private/no-store so replacing a
screenshot is immediately visible. The frontend's deployment must forward the user's
session/access token through its existing /api/eval proxy for native image requests.
That proxy and Kubernetes/ingress manifests are not present in this mirror repository.

## Rollout

1. Deploy backend first, with IMAGE_ATTACHMENTS_COLLECTION=image_attachments,
   SUGGESTION_IMAGE_MAX_FILES=3, SUGGESTION_IMAGE_MAX_BYTES=3145728, and
   PRODUCT_UPDATE_IMAGE_MAX_BYTES=3145728. Remove old GCS upload settings.
2. Deploy frontend. Keep the existing /api/eval forwarding configuration; allow multipart
   POST/PATCH bodies of at least 10 MiB end-to-end (three 3 MiB files plus form overhead).
3. Test text-only suggestions, three image attachments, HEIC conversion, rejection over
   3 MiB, unauthorized image GETs, and authenticated image GETs through the actual proxy.
4. No suggestion data migration is needed. For each existing What's New entry, use Edit
   and select the original screenshot to replace its legacy reference with MongoDB storage.
   Saving without a file preserves the existing reference. Replacement/deletion never
   calls GCP; legacy objects remain in the old bucket until bucket retirement.
5. Verify all existing screenshots, then retire upload-related GCP secrets and buckets.
   These PRs do not delete buckets, credentials, or existing data.

Image replacement keeps the previous image until the parent update succeeds and rolls back
the new upload on failure. Old MongoDB images are cleaned up after successful replacement.
Concurrent admin edits use last-write-wins and can leave an unreferenced image; coordinate
edits during handoff. No suggestion-delete endpoint exists in the mirror; any future
delete endpoint must clean up the referenced attachments.

## Review and validation

The company deployment has configuration and proxy wiring not represented by this mirror.
Apply these focused changes to its corresponding files rather than replacing unrelated auth,
routing, or deployment configuration. Run the full application checks in that environment.
