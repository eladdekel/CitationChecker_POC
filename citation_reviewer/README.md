# Citation Reviewer Web Application

A modern, full-featured web application for reviewing and managing citation data from court PDF documents. This tool allows users to browse processed files, examine individual citations with AI analysis scores, and mark citations as verified, fraudulent, or ignored.

---

## Features

### 📊 Dashboard
- Overview of all processed files and citations
- Real-time statistics on review progress
- Quick access to flagged and recently reviewed citations

### 📁 File Browser
- View all processed PDF files
- See file metadata (court number, style of cause, nature)
- Track review progress per file
- Drill down into individual files for detailed citation review

### 🔍 Citation Management
- Browse all citations across all files
- Filter by review status (unreviewed, verified, fraudulent, ignored)
- View AI analysis scores (relation score, pinpoint score)
- See AI reasoning and reason codes

### 🚩 Flagged Citations
- Automatic flagging of suspicious citations
- Low AI scores
- Out-of-jurisdiction flags
- Age mismatch flags
- Self-citation detection

### ✅ Review Workflow
- Mark citations as:
  - **OK** - Manually verified as correct
  - **Fraudulent** - Citation appears to be fabricated or incorrect
  - **Ignore** - Citation should be ignored (with reason)
- All reviews are stored in the PostgreSQL database

---

## Prerequisites

- **Node.js** 16.0 or higher
- **PostgreSQL** database (same as the main pipeline)
- Valid `.env` file in the parent directory with database credentials

---

## Installation

1. Navigate to the citation_reviewer directory:
   ```bash
   cd citation_reviewer
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Ensure your parent directory's `.env` file contains the database credentials:
   ```
   DB_HOST=your-database-host
   DB_PORT=5432
   DB_NAME=your-database-name
   DB_USER=your-username
   DB_PASSWORD=your-password
   DB_SSL=true
   ```

---

## Usage

### Starting the Server

```bash
npm start
```

Or for development:

```bash
npm run dev
```

The server will start on **http://localhost:3000** by default.

### Changing the Port

Set the `REVIEWER_PORT` environment variable:

```bash
REVIEWER_PORT=8080 npm start
```

---

## Database Schema

This application uses the `citation_reviews` table (created automatically on first run):

| Column | Type | Description |
|--------|------|-------------|
| `filename` | TEXT | PDF filename (e.g., `3076265.pdf`) |
| `citation` | TEXT | Citation string (e.g., `2022 FC 1478`) |
| `instance_id` | TEXT | Unique ID for each citation instance |
| `status` | TEXT | One of: `ok`, `fraud`, `ignore` |
| `reason` | TEXT | Optional reason for fraud/ignore decisions |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

The table has a composite primary key of `(filename, citation, instance_id)`.

---

## API Endpoints

### Files

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/files` | GET | List all files with summary data |
| `/api/files/:filename` | GET | Get full file data with citations |

### Citations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/citations` | GET | Get all citations (supports `?status=` and `?flagged=true` filters) |

### Reviews

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/reviews` | POST | Submit a citation review |
| `/api/reviews` | DELETE | Remove a citation review |

### Statistics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stats` | GET | Get overall statistics |

---

## Review Statuses

| Status | Description | Reason Required |
|--------|-------------|-----------------|
| `ok` | Citation verified as correct | No |
| `fraud` | Citation appears fraudulent | Yes |
| `ignore` | Citation should be ignored | Yes |

---

## Data Flow

1. The main pipeline processes PDFs and stores results in `file_outputs` table
2. This web app reads from `file_outputs` to display citations
3. Users review citations through the web interface
4. Reviews are stored in `citation_reviews` table
5. Reviews can be queried and exported for further analysis

---

## Technology Stack

- **Backend**: Node.js with Express.js
- **Database**: PostgreSQL with `pg` driver
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **Styling**: Custom CSS with glassmorphism, dark theme, and responsive design

---

## Project Structure

```
citation_reviewer/
├── server.js          # Express server and API routes
├── package.json       # Node.js dependencies
├── README.md          # This file
└── public/            # Static frontend files
    ├── index.html     # Main HTML file
    ├── styles.css     # CSS styles
    └── app.js         # Frontend JavaScript
```

---

## Screenshots

The application features:
- Dark mode interface with glassmorphism effects
- Responsive sidebar navigation
- Interactive stat cards
- Detailed citation cards with AI analysis
- Modal dialogs for reviewing and viewing citation details

---

## Troubleshooting

### Database Connection Issues

1. Ensure the `.env` file exists in the parent directory
2. Verify database credentials are correct
3. Check that `DB_SSL=true` if connecting to Azure PostgreSQL

### No Data Showing

1. Ensure the main pipeline has processed some PDFs
2. Check that data exists in the `file_outputs` table
3. Look for errors in the server console

### Port Already in Use

Change the port using the `REVIEWER_PORT` environment variable.

---

## License

This project is part of the Citation Analysis Pipeline.
