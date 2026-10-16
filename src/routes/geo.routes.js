import { Router } from 'express';
import fs from 'fs';
import config from '../config/index.js';

const router = Router();

let indianGeoData = {};
try {
  if (fs.existsSync(config.GEO_DATA_PATH)) {
    const raw = fs.readFileSync(config.GEO_DATA_PATH, 'utf-8');
    indianGeoData = JSON.parse(raw);
  }
} catch (err) {
  console.warn('[Geo Routes] Could not read india_geo_data.json:', err.message);
}

// GET /api/geo/states
router.get('/states', (req, res) => {
  const states = Object.keys(indianGeoData).sort();
  res.json(states);
});

// GET /api/geo/cities?state=...
router.get('/cities', (req, res) => {
  const stateQuery = req.query.state ? String(req.query.state).trim() : '';
  if (!stateQuery || !indianGeoData[stateQuery]) {
    return res.status(404).json({
      detail: `State '${stateQuery}' not found in registry.`
    });
  }
  const cities = (indianGeoData[stateQuery] || []).slice().sort();
  res.json(cities);
});

export default router;
