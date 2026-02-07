const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const express = require('express');
const axios = require('axios');
const qrcode = require('qrcode-terminal');
const pino = require('pino');
const fs = require('fs');
const path = require('path');

const multer = require('multer');
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 50 * 1024 * 1024 } });

const app = express();
app.use(express.json({ limit: '50mb' }));

const PORT = process.env.PORT || 3002;
const AUTH_DIR = path.join(__dirname, 'auth_info');

let sock = null;
let qrCodeData = null;
let clientReady = false;

async function connectToWhatsApp() {
    try {
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

        const logger = pino({ level: 'warn' });

        sock = makeWASocket({
            auth: state,
            printQRInTerminal: true,
            logger,
            browser: ['GeekTechnologie', 'Chrome', '120.0.0'],
        });

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', (update) => {
            const { connection, lastDisconnect, qr } = update;
            console.log('Connection update:', JSON.stringify({ connection, qr: qr ? 'present' : 'absent', statusCode: lastDisconnect?.error?.output?.statusCode, error: lastDisconnect?.error?.message, errorData: lastDisconnect?.error?.data }));

        if (qr) {
            console.log('QR Code recu, scannez-le avec WhatsApp:');
            qrcode.generate(qr, { small: true });
            qrCodeData = qr;
        }

        if (connection === 'close') {
            clientReady = false;
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            console.log('Connexion fermee. Code:', statusCode, '- Reconnexion:', shouldReconnect);
            if (shouldReconnect) {
                setTimeout(() => connectToWhatsApp(), 3000);
            } else {
                console.log('Session deconnectee. Suppression auth_info pour nouveau QR code...');
                fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                fs.mkdirSync(AUTH_DIR, { recursive: true });
                setTimeout(() => connectToWhatsApp(), 3000);
            }
        } else if (connection === 'open') {
            console.log('WhatsApp connecte!');
            clientReady = true;
            qrCodeData = null;
        }
    });
    } catch (err) {
        console.error('Erreur connectToWhatsApp:', err);
        setTimeout(() => connectToWhatsApp(), 5000);
    }
}

function formatChatId(phone) {
    let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
    if (!chatId.endsWith('@s.whatsapp.net')) {
        chatId = chatId + '@s.whatsapp.net';
    }
    return chatId;
}

// API Endpoints

// Status
app.get('/api/status', (req, res) => {
    res.json({
        status: clientReady ? 'ready' : 'not_ready',
        qrCode: qrCodeData ? true : false
    });
});

// QR code
app.get('/api/qr', (req, res) => {
    if (clientReady) {
        return res.json({ status: 'already_connected' });
    }
    if (qrCodeData) {
        return res.json({ qr: qrCodeData });
    }
    res.json({ status: 'waiting_for_qr' });
});

// Send text message
app.post('/api/sendText', async (req, res) => {
    try {
        const { phone, text } = req.body;

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !text) {
            return res.status(400).json({ error: 'phone et text requis' });
        }

        const chatId = formatChatId(phone);
        const result = await sock.sendMessage(chatId, { text });
        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi texte:', error);
        res.status(500).json({ error: error.message });
    }
});

// Send PDF from base64 buffer (new endpoint for Gotenberg integration)
app.post('/api/sendPdfBuffer', async (req, res) => {
    try {
        const { phone, pdfBase64, filename, caption } = req.body;

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !pdfBase64) {
            return res.status(400).json({ error: 'phone et pdfBase64 requis' });
        }

        const chatId = formatChatId(phone);
        const pdfBuffer = Buffer.from(pdfBase64, 'base64');

        console.log(`Envoi PDF (${pdfBuffer.length} bytes) a ${chatId}`);

        const result = await sock.sendMessage(chatId, {
            document: pdfBuffer,
            mimetype: 'application/pdf',
            fileName: filename || 'document.pdf',
            caption: caption || ''
        });

        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi PDF buffer:', error);
        res.status(500).json({ error: error.message });
    }
});

// Send PDF from file upload (multipart form-data - for n8n binary integration)
app.post('/api/sendPdfFile', upload.single('pdf'), async (req, res) => {
    try {
        const phone = req.body.phone;
        const filename = req.body.filename || 'document.pdf';
        const caption = req.body.caption || '';

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !req.file) {
            return res.status(400).json({ error: 'phone et fichier pdf requis' });
        }

        const chatId = formatChatId(phone);
        const pdfBuffer = req.file.buffer;

        console.log(`Envoi PDF upload (${pdfBuffer.length} bytes) a ${chatId}`);

        const result = await sock.sendMessage(chatId, {
            document: pdfBuffer,
            mimetype: 'application/pdf',
            fileName: filename,
            caption: caption
        });

        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi PDF file:', error);
        res.status(500).json({ error: error.message });
    }
});

// Send PDF from URL (generates PDF via Puppeteer - kept for backward compat)
app.post('/api/sendPdf', async (req, res) => {
    try {
        const { phone, htmlUrl, filename, caption } = req.body;

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !htmlUrl) {
            return res.status(400).json({ error: 'phone et htmlUrl requis' });
        }

        const chatId = formatChatId(phone);

        console.log(`Telechargement PDF depuis: ${htmlUrl}`);

        // Download the file from URL
        const response = await axios.get(htmlUrl, {
            responseType: 'arraybuffer',
            timeout: 30000
        });

        const pdfBuffer = Buffer.from(response.data);
        console.log(`PDF telecharge: ${pdfBuffer.length} bytes`);

        const result = await sock.sendMessage(chatId, {
            document: pdfBuffer,
            mimetype: 'application/pdf',
            fileName: filename || 'document.pdf',
            caption: caption || ''
        });

        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi PDF:', error);
        res.status(500).json({ error: error.message });
    }
});

// Send file from URL
app.post('/api/sendFile', async (req, res) => {
    try {
        const { phone, fileUrl, filename, caption } = req.body;

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !fileUrl) {
            return res.status(400).json({ error: 'phone et fileUrl requis' });
        }

        const chatId = formatChatId(phone);

        const response = await axios.get(fileUrl, {
            responseType: 'arraybuffer',
            timeout: 30000
        });

        const fileBuffer = Buffer.from(response.data);
        const mimeType = response.headers['content-type'] || 'application/octet-stream';

        const result = await sock.sendMessage(chatId, {
            document: fileBuffer,
            mimetype: mimeType,
            fileName: filename || 'document',
            caption: caption || ''
        });

        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi fichier:', error);
        res.status(500).json({ error: error.message });
    }
});

// Send image from URL
app.post('/api/sendImage', async (req, res) => {
    try {
        const { phone, imageUrl, caption } = req.body;

        if (!clientReady || !sock) {
            return res.status(503).json({ error: 'WhatsApp non connecte' });
        }
        if (!phone || !imageUrl) {
            return res.status(400).json({ error: 'phone et imageUrl requis' });
        }

        const chatId = formatChatId(phone);

        const response = await axios.get(imageUrl, {
            responseType: 'arraybuffer',
            timeout: 30000
        });

        const imageBuffer = Buffer.from(response.data);
        const mimeType = response.headers['content-type'] || 'image/jpeg';

        const result = await sock.sendMessage(chatId, {
            image: imageBuffer,
            mimetype: mimeType,
            caption: caption || ''
        });

        res.json({ success: true, messageId: result.key.id });
    } catch (error) {
        console.error('Erreur envoi image:', error);
        res.status(500).json({ error: error.message });
    }
});

// Restart
app.post('/api/restart', async (req, res) => {
    try {
        console.log('Demande de redemarrage recue');
        res.json({ success: true, message: 'Redemarrage en cours...' });
        clientReady = false;
        qrCodeData = null;
        if (sock) {
            await sock.logout().catch(() => {});
        }
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        fs.mkdirSync(AUTH_DIR, { recursive: true });
        setTimeout(() => connectToWhatsApp(), 2000);
    } catch (error) {
        console.error('Erreur redemarrage:', error);
        res.status(500).json({ error: error.message });
    }
});

// Start
app.listen(PORT, '0.0.0.0', () => {
    console.log(`Service WhatsApp Baileys demarre sur le port ${PORT}`);
    console.log('Endpoints disponibles:');
    console.log('   GET  /api/status - Statut du service');
    console.log('   GET  /api/qr - Obtenir le QR code');
    console.log('   POST /api/sendText - Envoyer un message texte');
    console.log('   POST /api/sendPdfBuffer - Envoyer un PDF (base64)');
    console.log('   POST /api/sendPdf - Envoyer un PDF depuis URL');
    console.log('   POST /api/sendFile - Envoyer un fichier');
    console.log('   POST /api/sendImage - Envoyer une image');
    console.log('   POST /api/restart - Redemarrer le service');
});

connectToWhatsApp();
