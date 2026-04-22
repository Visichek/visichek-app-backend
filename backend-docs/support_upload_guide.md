# Support Case Attachments — Frontend Upload Guide

Attaching a file to a support case is a **3-step presigned-upload flow**. You cannot skip a step: step 1 only issues an upload slot, step 2 puts the bytes in storage, and step 3 is what actually registers the attachment on the case thread.

---

## Flow at a glance

```
[1] POST /v1/support-cases/{caseId}/attachments/intent   -> { upload_url, object_key, ... }
[2] PUT  <upload_url>                                    -> uploads the file bytes directly to storage
[3] POST /v1/support-cases/{caseId}/attachments          -> registers the attachment on the case (202)
```

Only after step 3 does the attachment appear on the case for other participants.

---

## Step 1 — Request an upload intent

Ask the API for a presigned upload slot.

**Endpoint**

```
POST /v1/support-cases/{caseId}/attachments/intent
Authorization: Bearer <access_token>
Content-Type: application/json
```

**Body**

```json
{
  "file_name": "screenshot.png",
  "mime_type": "image/png",
  "size": 184320
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `file_name` | yes | 1–256 chars. Just the display name, no path. |
| `mime_type` | no  | Defaults to `application/octet-stream` if omitted. Send the browser's `File.type` when you have it. |
| `size` | no | In bytes. Send `File.size` when available. |

**Response (200)**

```json
{
  "success": true,
  "data": {
    "upload_url": "https://.../s3-presigned-url?...",
    "object_key": "support-cases/69e8.../a8c1...png",
    "method": "PUT",
    "headers": { "Content-Type": "image/png" },
    "expires_in": 900
  }
}
```

Hold on to `object_key` — step 3 needs it. `expires_in` is seconds; after that the upload URL is dead and you have to ask for a new one.

---

## Step 2 — Upload the bytes directly to storage

Send the raw file to `upload_url` using the method and headers the API returned. **Do not** send this through the API server and **do not** add an `Authorization` header — the presigned URL already carries its own credentials.

```js
async function uploadBytes(intent, file) {
  const res = await fetch(intent.upload_url, {
    method: intent.method,          // "PUT"
    headers: intent.headers,        // usually { "Content-Type": "<mime>" }
    body: file,                     // the File/Blob itself, not FormData
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
}
```

Gotchas:

- **Don't wrap it in `FormData`.** S3-style presigned PUTs want the raw body.
- **Send exactly the headers the API returned**, nothing more. Extra headers (especially `x-amz-*` or `Authorization`) will make S3 reject the request with a signature mismatch.
- If you need a progress bar, use `XMLHttpRequest` with `upload.onprogress` — `fetch` can't report upload progress.

---

## Step 3 — Register the attachment on the case

Tell the API the upload finished. This is what actually puts the attachment on the case thread.

**Endpoint**

```
POST /v1/support-cases/{caseId}/attachments
Authorization: Bearer <access_token>
Content-Type: application/json
```

**Body**

```json
{
  "body": "Here's the screenshot of the error.",
  "attachments": [
    {
      "document_id": "<any stable id you want to reference it by>",
      "file_name": "screenshot.png",
      "mime_type": "image/png",
      "size": 184320,
      "object_key": "support-cases/69e8.../a8c1...png"
    }
  ]
}
```

| Field | Notes |
|-------|-------|
| `body` | Required, 1–20 000 chars. The message text that carries the attachment. Use a short caption if the user didn't type anything. |
| `attachments[].object_key` | **Must** match the `object_key` from step 1. |
| `attachments[].document_id` | A client-side id used to reference the attachment in the thread. |
| `attachments[]` other fields | Echo the `file_name` / `mime_type` / `size` you sent in step 1. |

**Response (202 Accepted)**

```json
{
  "success": true,
  "data": {
    "id": "...",
    "jobId": "...",
    "status": "queued"
  }
}
```

The write is queued, not immediate. Two options:

1. Optimistic UI — show the attachment right away and trust the queue.
2. Poll `GET /v1/jobs/{jobId}` until `status` is `succeeded` (or `failed`), then refresh the case.

---

## Putting it together

```js
async function attachFileToCase(caseId, file, caption, accessToken) {
  const auth = { Authorization: `Bearer ${accessToken}` };

  // 1. intent
  const intentRes = await fetch(
    `/v1/support-cases/${caseId}/attachments/intent`,
    {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({
        file_name: file.name,
        mime_type: file.type || "application/octet-stream",
        size: file.size,
      }),
    },
  );
  const { data: intent } = await intentRes.json();

  // 2. direct upload to storage
  const putRes = await fetch(intent.upload_url, {
    method: intent.method,
    headers: intent.headers,
    body: file,
  });
  if (!putRes.ok) throw new Error(`Upload failed: ${putRes.status}`);

  // 3. register
  const registerRes = await fetch(
    `/v1/support-cases/${caseId}/attachments`,
    {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({
        body: caption || file.name,
        attachments: [
          {
            document_id: crypto.randomUUID(),
            file_name: file.name,
            mime_type: file.type || "application/octet-stream",
            size: file.size,
            object_key: intent.object_key,
          },
        ],
      }),
    },
  );
  return registerRes.json(); // { data: { jobId, status: "queued", ... } }
}
```

---

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Step 1 returns `503 Document storage is not configured` | Backend storage backend isn't wired up in this environment. Not a frontend bug. |
| Step 2 returns `403 SignatureDoesNotMatch` | You added/removed headers, wrapped the body in `FormData`, or changed `Content-Type`. Send exactly what the intent returned. |
| Step 2 returns `403 Request has expired` | Step 1 was issued more than `expires_in` seconds ago. Re-request the intent. |
| Step 3 returns `202` but the attachment never shows up | Poll `GET /v1/jobs/{jobId}` — the writer may have failed (wrong `object_key`, etc.). |
| Step 3 returns `404` | Wrong `caseId`, or the case belongs to a different tenant than the caller. |
