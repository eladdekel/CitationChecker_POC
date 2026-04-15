/**
 * Citation Reviewer Server
 * Express.js backend for viewing and reviewing citation data
 */

require('dotenv').config({ path: '../.env' });
const express = require('express');
const { Pool } = require('pg');
const path = require('path');
const crypto = require('crypto');

const app = express();
const PORT = process.env.REVIEWER_PORT || 3000;

// Middleware
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// PostgreSQL connection pool
const pool = new Pool({
  host: process.env.DB_HOST,
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false,
});

// Initialize database tables
async function initializeDatabase() {
  const client = await pool.connect();
  try {
    // Create citation_reviews table if it doesn't exist
    await client.query(`
      CREATE TABLE IF NOT EXISTS citation_reviews (
        filename TEXT NOT NULL,
        citation TEXT NOT NULL,
        instance_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ok', 'fraud', 'ignore')),
        reason TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (filename, citation, instance_id)
      )
    `);
    console.log('Database tables initialized');
  } catch (err) {
    console.error('Error initializing database:', err);
  } finally {
    client.release();
  }
}

// Generate instance_id from instance data
function generateInstanceId(instance) {
  const data = JSON.stringify({
    paragraph: instance.paragraph?.substring(0, 200) || '',
    page: instance.page,
    pinpoints: instance.pinpoints || []
  });
  return crypto.createHash('md5').update(data).digest('hex').substring(0, 16);
}

// API Routes

// Get all files with summary data
app.get('/api/files', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT 
        fo.filename,
        fo.payload->>'court_no' as court_no,
        fo.payload->>'style_of_cause' as style_of_cause,
        fo.payload->>'english_nature_desc' as nature_desc,
        (fo.payload->>'unique_citations')::int as unique_citations,
        (fo.payload->>'total_citations')::int as total_citations,
        fo.updated_at,
        COALESCE(review_counts.reviewed, 0) as reviewed_count,
        COALESCE(review_counts.ok_count, 0) as ok_count,
        COALESCE(review_counts.fraud_count, 0) as fraud_count,
        COALESCE(review_counts.ignore_count, 0) as ignore_count
      FROM file_outputs fo
      LEFT JOIN (
        SELECT 
          filename,
          COUNT(*) as reviewed,
          COUNT(*) FILTER (WHERE status = 'ok') as ok_count,
          COUNT(*) FILTER (WHERE status = 'fraud') as fraud_count,
          COUNT(*) FILTER (WHERE status = 'ignore') as ignore_count
        FROM citation_reviews
        GROUP BY filename
      ) review_counts ON fo.filename = review_counts.filename
      ORDER BY fo.updated_at DESC
    `);
    res.json(result.rows);
  } catch (err) {
    console.error('Error fetching files:', err);
    res.status(500).json({ error: 'Failed to fetch files' });
  }
});

// Get a single file with all citation data
app.get('/api/files/:filename', async (req, res) => {
  try {
    const { filename } = req.params;
    const result = await pool.query(
      'SELECT payload, updated_at FROM file_outputs WHERE filename = $1',
      [filename]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'File not found' });
    }
    
    const payload = result.rows[0].payload;
    
    // Get existing reviews for this file
    const reviewsResult = await pool.query(
      'SELECT citation, instance_id, status, reason, updated_at FROM citation_reviews WHERE filename = $1',
      [filename]
    );
    
    // Map reviews by citation+instance_id
    const reviewMap = {};
    reviewsResult.rows.forEach(r => {
      reviewMap[`${r.citation}:${r.instance_id}`] = {
        status: r.status,
        reason: r.reason,
        updated_at: r.updated_at
      };
    });
    
    // Attach review status to each instance
    if (payload.results) {
      payload.results.forEach(citation => {
        if (citation.instances) {
          citation.instances.forEach(instance => {
            const instanceId = generateInstanceId(instance);
            instance.instance_id = instanceId;
            const key = `${citation.citation}:${instanceId}`;
            if (reviewMap[key]) {
              instance.review = reviewMap[key];
            }
          });
        }
      });
    }
    
    res.json({
      ...payload,
      updated_at: result.rows[0].updated_at
    });
  } catch (err) {
    console.error('Error fetching file:', err);
    res.status(500).json({ error: 'Failed to fetch file' });
  }
});

// Get all citations across all files
app.get('/api/citations', async (req, res) => {
  try {
    const { status, flagged } = req.query;
    
    const result = await pool.query(`
      SELECT 
        fo.filename,
        fo.payload->>'court_no' as court_no,
        fo.payload->>'style_of_cause' as style_of_cause,
        fo.payload->'results' as citations
      FROM file_outputs fo
      ORDER BY fo.updated_at DESC
    `);
    
    // Flatten citations and attach reviews
    const allCitations = [];
    
    for (const row of result.rows) {
      if (!row.citations) continue;
      
      // Get reviews for this file
      const reviewsResult = await pool.query(
        'SELECT citation, instance_id, status, reason FROM citation_reviews WHERE filename = $1',
        [row.filename]
      );
      const reviewMap = {};
      reviewsResult.rows.forEach(r => {
        reviewMap[`${r.citation}:${r.instance_id}`] = { status: r.status, reason: r.reason };
      });
      
      for (const citation of row.citations) {
        if (citation.instances) {
          for (const instance of citation.instances) {
            const instanceId = generateInstanceId(instance);
            const key = `${citation.citation}:${instanceId}`;
            const review = reviewMap[key];
            
            // Filter by status if requested
            if (status && (!review || review.status !== status)) continue;
            
            // Filter by flagged (has low GPT scores or flags)
            if (flagged === 'true') {
              const relScore = instance.gpt_relation_score;
              const pinScore = instance.gpt_pinpoint_score;
              const isFlagged = 
                instance.gpt_below_threshold === true ||
                instance.out_of_jurisdiction_flag === true ||
                instance.age_mismatch_flag === true ||
                instance.self_citation_docket_flag === true ||
                (relScore !== null && relScore !== undefined && relScore < 0.6) ||
                (pinScore !== null && pinScore !== undefined && pinScore < 0.6);
              if (!isFlagged) continue;
            }
            
            allCitations.push({
              filename: row.filename,
              court_no: row.court_no,
              style_of_cause: row.style_of_cause,
              citation: citation.citation,
              citation_normalized: citation.citation_normalized,
              instance_id: instanceId,
              page: instance.page,
              paragraph: instance.paragraph,
              pinpoints: instance.pinpoints,
              gpt_relation_score: instance.gpt_relation_score,
              gpt_pinpoint_score: instance.gpt_pinpoint_score,
              gpt_relation_reasoning: instance.gpt_relation_reasoning,
              gpt_reason_code: instance.gpt_reason_code,
              gpt_below_threshold: instance.gpt_below_threshold,
              out_of_jurisdiction_flag: instance.out_of_jurisdiction_flag,
              age_mismatch_flag: instance.age_mismatch_flag,
              self_citation_docket_flag: instance.self_citation_docket_flag,
              canlii_data: citation.canlii_api_response,
              hf_result: citation.hf_result,
              review: review || null
            });
          }
        }
      }
    }
    
    res.json(allCitations);
  } catch (err) {
    console.error('Error fetching citations:', err);
    res.status(500).json({ error: 'Failed to fetch citations' });
  }
});

// Submit a review for a citation instance
app.post('/api/reviews', async (req, res) => {
  try {
    const { filename, citation, instance_id, status, reason } = req.body;
    
    if (!filename || !citation || !instance_id || !status) {
      return res.status(400).json({ error: 'Missing required fields' });
    }
    
    if (!['ok', 'fraud', 'ignore'].includes(status)) {
      return res.status(400).json({ error: 'Invalid status. Must be ok, fraud, or ignore' });
    }
    
    await pool.query(`
      INSERT INTO citation_reviews (filename, citation, instance_id, status, reason, updated_at)
      VALUES ($1, $2, $3, $4, $5, NOW())
      ON CONFLICT (filename, citation, instance_id)
      DO UPDATE SET status = $4, reason = $5, updated_at = NOW()
    `, [filename, citation, instance_id, status, reason || null]);
    
    res.json({ success: true, message: 'Review saved' });
  } catch (err) {
    console.error('Error saving review:', err);
    res.status(500).json({ error: 'Failed to save review' });
  }
});

// Get review statistics
app.get('/api/stats', async (req, res) => {
  try {
    const [filesResult, reviewsResult, citationsResult] = await Promise.all([
      pool.query('SELECT COUNT(*) as count FROM file_outputs'),
      pool.query(`
        SELECT 
          COUNT(*) as total,
          COUNT(*) FILTER (WHERE status = 'ok') as ok_count,
          COUNT(*) FILTER (WHERE status = 'fraud') as fraud_count,
          COUNT(*) FILTER (WHERE status = 'ignore') as ignore_count
        FROM citation_reviews
      `),
      pool.query(`
        SELECT 
          SUM((payload->>'total_citations')::int) as total_instances,
          SUM((payload->>'unique_citations')::int) as unique_citations
        FROM file_outputs
      `)
    ]);
    
    res.json({
      total_files: parseInt(filesResult.rows[0].count),
      total_citation_instances: parseInt(citationsResult.rows[0].total_instances) || 0,
      unique_citations: parseInt(citationsResult.rows[0].unique_citations) || 0,
      reviews: {
        total: parseInt(reviewsResult.rows[0].total) || 0,
        ok: parseInt(reviewsResult.rows[0].ok_count) || 0,
        fraud: parseInt(reviewsResult.rows[0].fraud_count) || 0,
        ignore: parseInt(reviewsResult.rows[0].ignore_count) || 0
      }
    });
  } catch (err) {
    console.error('Error fetching stats:', err);
    res.status(500).json({ error: 'Failed to fetch stats' });
  }
});

// Delete a review
app.delete('/api/reviews', async (req, res) => {
  try {
    const { filename, citation, instance_id } = req.body;
    
    await pool.query(
      'DELETE FROM citation_reviews WHERE filename = $1 AND citation = $2 AND instance_id = $3',
      [filename, citation, instance_id]
    );
    
    res.json({ success: true, message: 'Review deleted' });
  } catch (err) {
    console.error('Error deleting review:', err);
    res.status(500).json({ error: 'Failed to delete review' });
  }
});

// Serve the main app
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Start server
async function startServer() {
  await initializeDatabase();
  app.listen(PORT, () => {
    console.log(`
╔═══════════════════════════════════════════════════════════╗
║           Citation Reviewer Server Started                 ║
╠═══════════════════════════════════════════════════════════╣
║  Local:   http://localhost:${PORT}                           ║
║                                                           ║
║  Features:                                                ║
║    • Browse all processed PDF files                       ║
║    • View citation details and AI analysis                ║
║    • Mark citations as OK, Fraudulent, or Ignore          ║
║    • Track review progress and statistics                 ║
╚═══════════════════════════════════════════════════════════╝
    `);
  });
}

startServer().catch(console.error);
