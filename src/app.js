import express from 'express';
import cors from 'cors';
import path from 'path';
import config from './config/index.js';

import healthRoutes from './routes/health.routes.js';
import geoRoutes from './routes/geo.routes.js';
import authRoutes from './routes/auth.routes.js';
import applicationsRoutes from './routes/applications.routes.js';
import documentsRoutes from './routes/documents.routes.js';

export function createApp() {
  const app = express();

  // CORS configuration
  app.use(
    cors({
      origin: (origin, callback) => {
        // Allow requests with no origin (like mobile apps, curl, or server-to-server)
        if (!origin) return callback(null, true);
        if (config.ALLOWED_ORIGINS.indexOf(origin) !== -1 || config.ALLOWED_ORIGINS.includes('*')) {
          return callback(null, true);
        }
        // Allow any localhost/127.0.0.1 port in development
        if (/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
          return callback(null, true);
        }
        return callback(null, true); // Dev-friendly fallback
      },
      credentials: true
    })
  );

  // Body parsers
  app.use(express.json({ limit: '25mb' }));
  app.use(express.urlencoded({ extended: true, limit: '25mb' }));

  // Request logger
  app.use((req, res, next) => {
    const start = Date.now();
    res.on('finish', () => {
      const duration = Date.now() - start;
      if (req.path.startsWith('/api')) {
        console.log(`[API] ${req.method} ${req.originalUrl} ${res.statusCode} (${duration}ms)`);
      }
    });
    next();
  });

  // Mount API route modules
  app.use('/api', healthRoutes);
  app.use('/api/geo', geoRoutes);
  app.use('/api', authRoutes);
  app.use('/api', applicationsRoutes);
  app.use('/api', documentsRoutes);

  // Serve static files from root and public directories
  app.use(express.static(path.join(config.ROOT_DIR, 'public')));
  app.use(express.static(config.ROOT_DIR));

  // Root route handler
  app.get('/', (req, res) => {
    if (req.accepts('html')) {
      return res.sendFile(path.join(config.ROOT_DIR, 'index.html'));
    }
    res.json({
      name: 'CREDORA API',
      description: 'Autonomous AI Underwriting Assistant & Loan Workflow Platform',
      version: '1.2.0',
      endpoints: {
        health: '/api/health',
        states: '/api/geo/states',
        cities: '/api/geo/cities?state=Maharashtra',
        sendOtp: 'POST /api/application/send-otp',
        verifyOtp: 'POST /api/application/verify-otp',
        registerBorrower: 'POST /api/auth/register-borrower',
        registerLender: 'POST /api/auth/register-lender',
        analyzeDocuments: 'POST /api/documents/analyze',
        submitApplication: 'POST /api/applications',
        listApplications: 'GET /api/applications',
        getApplication: 'GET /api/applications/:id'
      }
    });
  });

  // Handle 404s for API routes
  app.use('/api', (req, res) => {
    res.status(404).json({
      error: 'Not Found',
      detail: `API endpoint '${req.method} ${req.originalUrl}' does not exist.`
    });
  });

  // Global Error Handler
  app.use((err, req, res, next) => {
    console.error('[Global Error]', err);
    const status = err.status || 500;
    res.status(status).json({
      error: err.name || 'InternalServerError',
      detail: err.message || 'An unexpected server error occurred.'
    });
  });

  return app;
}

export default createApp;
