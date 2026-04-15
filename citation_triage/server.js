/**
 * Citation Triage Server
 * Lightweight Express server — loads all data in ONE query, reviews persist to DB.
 */

require('dotenv').config({ path: '../.env' });
const express = require('express');
const { Pool } = require('pg');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const pool = new Pool({
    host: process.env.DB_HOST,
    port: parseInt(process.env.DB_PORT || '5432'),
    database: process.env.DB_NAME,
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false,
});

// Ensure review tables exist
async function initDB() {
    await pool.query(`
    CREATE TABLE IF NOT EXISTS citation_reviews (
      id SERIAL PRIMARY KEY,
      filename TEXT NOT NULL,
      citation TEXT NOT NULL,
      instance_id TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('problematic','fine','ignored')),
      reason TEXT DEFAULT '',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(filename, citation, instance_id)
    );
  `);
    console.log('DB ready');
}
initDB().catch(err => console.error('DB init error:', err));

// Helper: deterministic instance_id from instance data
function instanceId(inst) {
    return `p${inst.page || 0}_${(inst.paragraph || '').slice(0, 60).replace(/[^a-zA-Z0-9]/g, '')}`;
}

// ── GET /api/all ──────────────────────────────────────────────
// Single query: returns every file's payload + all reviews in one shot.
app.get('/api/all', async (req, res) => {
    try {
        const [dataRes, reviewRes] = await Promise.all([
            pool.query('SELECT filename, payload FROM file_outputs ORDER BY filename'),
            pool.query('SELECT filename, citation, instance_id, status, reason, updated_at FROM citation_reviews'),
        ]);

        // Build review lookup
        const reviewMap = {};
        for (const r of reviewRes.rows) {
            reviewMap[`${r.filename}|${r.citation}|${r.instance_id}`] = {
                status: r.status,
                reason: r.reason,
                updated_at: r.updated_at,
            };
        }

        // Flatten into rows
        const rows = [];
        for (const fileRow of dataRes.rows) {
            const p = typeof fileRow.payload === 'string' ? JSON.parse(fileRow.payload) : fileRow.payload;
            if (!p || !p.results) continue;

            const fileMeta = {
                filename: fileRow.filename,
                court_no: p.court_no || '',
                style_of_cause: p.style_of_cause || '',
                nature_of_proceedings: p.nature_of_proceedings || '',
                file_ai_summary: p.file_ai_summary || '',
            };

            for (const cit of p.results) {
                if (!cit.instances) continue;
                for (const inst of cit.instances) {
                    const iid = instanceId(inst);
                    const key = `${fileRow.filename}|${cit.citation}|${iid}`;
                    rows.push({
                        ...fileMeta,
                        citation: cit.citation,
                        canlii_title: (cit.canlii_api_response || {}).title || '',
                        canlii_url: (cit.canlii_api_response || {}).url || '',
                        instance_id: iid,
                        page: inst.page,
                        paragraph: inst.paragraph || '',
                        gpt_relation_score: inst.gpt_relation_score,
                        gpt_relation_reasoning: inst.gpt_relation_reasoning || '',
                        gpt_reason_code: inst.gpt_reason_code || '',
                        gpt_below_threshold: !!inst.gpt_below_threshold,
                        gpt_self_citation: !!inst.gpt_self_citation,
                        gpt_pass: inst.gpt_pass,
                        keyword_overlap: inst.keyword_overlap,
                        age_mismatch_flag: !!inst.age_mismatch_flag,
                        out_of_jurisdiction_flag: !!inst.out_of_jurisdiction_flag,
                        self_citation_docket_flag: !!inst.self_citation_docket_flag,
                        name_in_excerpt: inst.name_in_excerpt,
                        review: reviewMap[key] || null,
                    });
                }
            }
        }

        res.json({ rows, fileCount: dataRes.rows.length });
    } catch (err) {
        console.error('GET /api/all error:', err);
        res.status(500).json({ error: err.message });
    }
});

// ── POST /api/review ──────────────────────────────────────────
app.post('/api/review', async (req, res) => {
    try {
        const { filename, citation, instance_id, status, reason } = req.body;
        if (!filename || !citation || !instance_id || !status) {
            return res.status(400).json({ error: 'Missing required fields' });
        }
        await pool.query(`
      INSERT INTO citation_reviews (filename, citation, instance_id, status, reason, updated_at)
      VALUES ($1, $2, $3, $4, $5, NOW())
      ON CONFLICT (filename, citation, instance_id)
      DO UPDATE SET status = $4, reason = $5, updated_at = NOW()
    `, [filename, citation, instance_id, status, reason || '']);
        res.json({ ok: true });
    } catch (err) {
        console.error('POST /api/review error:', err);
        res.status(500).json({ error: err.message });
    }
});

// ── DELETE /api/review ────────────────────────────────────────
app.delete('/api/review', async (req, res) => {
    try {
        const { filename, citation, instance_id } = req.body;
        await pool.query(
            'DELETE FROM citation_reviews WHERE filename=$1 AND citation=$2 AND instance_id=$3',
            [filename, citation, instance_id]
        );
        res.json({ ok: true });
    } catch (err) {
        console.error('DELETE /api/review error:', err);
        res.status(500).json({ error: err.message });
    }
});

// ── POST /api/review/bulk ─────────────────────────────────────
app.post('/api/review/bulk', async (req, res) => {
    try {
        const { items, status, reason } = req.body;
        if (!items || !items.length || !status) {
            return res.status(400).json({ error: 'Missing required fields' });
        }
        const client = await pool.connect();
        try {
            await client.query('BEGIN');
            for (const item of items) {
                await client.query(`
          INSERT INTO citation_reviews (filename, citation, instance_id, status, reason, updated_at)
          VALUES ($1, $2, $3, $4, $5, NOW())
          ON CONFLICT (filename, citation, instance_id)
          DO UPDATE SET status = $4, reason = $5, updated_at = NOW()
        `, [item.filename, item.citation, item.instance_id, status, reason || '']);
            }
            await client.query('COMMIT');
        } catch (e) {
            await client.query('ROLLBACK');
            throw e;
        } finally {
            client.release();
        }
        res.json({ ok: true, count: items.length });
    } catch (err) {
        console.error('POST /api/review/bulk error:', err);
        res.status(500).json({ error: err.message });
    }
});

const PORT = process.env.TRIAGE_PORT || 3900;
app.listen(PORT, () => console.log(`Citation Triage → http://localhost:${PORT}`));
