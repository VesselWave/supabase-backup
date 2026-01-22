# Supabase Storage Migration & Backup Specification

This document details the technical implementation for a high-performance Supabase Storage migration, backup, and restore tool. It is designed to be language-agnostic, suitable for implementation in Node.js, Go, Rust, or Python.

## 1. Core Architecture

The system operates as a client-side orchestrator that interacts with the Supabase Storage API.

### 1.1 Base Configuration
*   **Target**: Supabase Storage API v1
*   **Base URL**: `https://<project_ref>.supabase.co/storage/v1`
*   **Authentication**:
    *   **Header**: `Authorization: Bearer <SERVICE_ROLE_KEY>`
    *   **Header**: `apikey: <SERVICE_ROLE_KEY>`
    *   *Rationale*: The Service Role Key is strictly required to bypass Row Level Security (RLS) policies, ensuring total visibility of all user files.

### 1.2 Resource Hierarchy
The migration tool must understand the storage hierarchy:
`Project` -> `Buckets` -> `Objects (Files/Folders)`

## 2. API Protocol Reference

All requests must include the Authentication headers defined above.

### 2.1 Bucket Operations

#### **List All Buckets**
*   **GET** `/bucket`
*   **Response**: `200 OK` - JSON Array
    ```json
    [
      {
        "id": "avatars",
        "name": "avatars",
        "owner": "utils_service_role",
        "public": true,
        "file_size_limit": null,
        "allowed_mime_types": ["image/*", "video/*"],
        "created_at": "2023-01-01T00:00:00Z",
        "updated_at": "2023-01-01T00:00:00Z"
      }
    ]
    ```

#### **Create Bucket**
*   **POST** `/bucket`
*   **Body**:
    ```json
    {
      "id": "string",
      "name": "string",
      "public": boolean,
      "file_size_limit": number | null,
      "allowed_mime_types": string[] | null
    }
    ```
*   **Behavior**: Idempotency is not guaranteed. If a bucket exists, this will return `400` or `409`. The tool should check existence via `List` or `Get` before creating.

### 2.2 Object Operations

#### **List Objects (Recursive Enumeration)**
The API lists a "directory" view. To get all files, the client must implement recursive traversal (Depth-First or Breadth-First).

*   **POST** `/object/list/{bucketName}`
*   **Headers**: `Content-Type: application/json`
*   **Body**:
    ```json
    {
      "prefix": "current/folder/path",
      "limit": 100,
      "offset": 0,
      "sortBy": { "column": "name", "order": "asc" }
    }
    ```
*   **Response Logic**:
    *   **File**: `{ "id": "uuid", "name": "file.ext", ... }`
    *   **Folder**: `{ "id": null, "name": "folder_name" }`

**Algorithm: `WalkDirectory(bucket, prefix)`**
1.  Initialize `offset = 0`, `limit = 100`.
2.  Loop:
    a.  Request `list({ prefix, limit, offset })`.
    b.  If empty response, break loop.
    c.  For each item:
        *   If `id == null`: Enqueue `WalkDirectory(bucket, prefix + "/" + item.name)` (Parallelizable).
        *   If `id != null`: Emit File Object for processing.
    d.  If `response.length < limit`, break loop.
    e.  `offset += limit`.

#### **Download Object**
*   **GET** `/object/{bucketName}/{wildcardPath}`
*   **Response**: Binary Stream.
*   **Error Handling**:
    *   `404 Not Found`: Log warning, skip.
    *   `5xx Server Error`: Retry with exponential backoff.

#### **Upload Object**
*   **POST** `/object/{bucketName}/{wildcardPath}`
*   **Headers**:
    *   `x-upsert`: `true` (Critical for migration/restore to avoid conflicts)
    *   `Content-Type`: Source file MIME type.
    *   `cache-control`: Source file Cache-Control header.
    *   `x-metadata`: Base64 encoded JSON string of custom metadata (optional).
*   **Body**: Binary Data.

## 3. Data Persistence Format (Backup)

When "backing up" to disk, the tool should maintain a structured format that allows for lossless restoration.

### 3.1 Directory Layout
```text
./backup_{timestamp}/
├── manifest.json              # Global configuration & bucket definitions
├── bucket_A/
│   ├── folder/
│   │   ├── image.png          # Raw binary content
│   │   └── image.png.meta     # Metadata JSON
│   └── document.pdf
│   └── document.pdf.meta
└── bucket_B/
    └── ...
```

### 3.2 File Formats

**`manifest.json`**
```json
{
  "version": "1.0",
  "timestamp": "2023-10-27T10:00:00Z",
  "source_project": "ref_code",
  "buckets": [
    {
      "id": "avatars",
      "public": true,
      "file_size_limit": null,
      "allowed_mime_types": null
    }
  ]
}
```

**`*.meta` (Sidecar JSON)**
```json
{
  "cacheControl": "3600",
  "contentType": "image/png",
  "metadata": {
    "size": 1024,
    "lastModified": "..."
  }
}
```

## 4. Implementation Strategies

### 4.1 Migration Mode (Stream Piping)
**Goal**: Maximize throughput, Minimize RAM usage.

*   **Pattern**: Producer-Consumer with a Bounded Channel.
*   **Concurrency**:
    *   **Listing Worker**: 1 thread per bucket (or recursive branch). Pushes found file paths to a `JobQueue`.
    *   **Transfer Workers**: N threads (e.g., 20) reading from `JobQueue`.
*   **Memory Management**:
    *   Use **Streams**. Pipe the Download Response Body directly to the Upload Request Body.
    *   *Do not* `await response.arrayBuffer()`—this creates GC pressure.
    *   Node.js: `response.body.pipe(uploadRequest)`.
    *   Go: `io.Copy(uploadRequest, downloadResponse)`.

### 4.2 Error Handling & Resilience
1.  **Network Flakiness**:
    *   Implement **Exponential Backoff** for `500`, `502`, `503`, `504` and `429` (Rate Limit) responses.
    *   Initial retry: 500ms, Multiplier: 1.5, Max Retries: 5.
2.  **404 on Download**:
    *   It is possible a file listed is deleted before download. Treat as non-fatal warning.
3.  **Large Files**:
    *   Verify timeout settings on the HTTP client. Uploads of 100MB+ files may take minutes. Set `timeout` to at least 10 minutes or use `0` (infinity) for body transfer.

### 4.3 Performance Tuning (Benchmarks)
*   **Parallelism**:
    *   Small files (<1MB): High concurrency (50+). Bottleneck is RTT/Latency.
    *   Large files (>50MB): Low concurrency (5-10). Bottleneck is Bandwidth.
*   **HTTP Agent**:
    *   Enable `Keep-Alive`.
    *   Node.js: `new https.Agent({ keepAlive: true, maxSockets: 50 })`.

## 5. Security & RLS Notes
*   **Policies**: This tool migrates **data**, not **permissions**.
*   SQL Row Level Security policies (permissions) are stored in the PostgreSQL `storage.objects` table logic. They must be migrated separately via SQL Dump (`pg_dump`).
*   The `service_role` key grants the migration tool access to *read* and *write* all files regardless of RLS, but once the file is in the new project, the new project's RLS policies will apply to users trying to access it.
