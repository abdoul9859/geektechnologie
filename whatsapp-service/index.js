const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;

app.get('/whatsapp', (req, res) => {
    res.sendFile(path.join(__dirname, 'whatsapp-connect.html'));
});

app.get('/whatsapp-connect.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'whatsapp-connect.html'));
});

// État du client WhatsApp
let clientReady = false;
let qrCodeData = null;

function _normalizeBase64(s) {
    if (!s) return s;
    let out = String(s).replace(/\s+/g, '');
    // Fix padding
    const pad = out.length % 4;
    if (pad === 2) out += '==';
    else if (pad === 3) out += '=';
    else if (pad === 1) {
        // If length mod 4 is 1, the string is invalid; best effort: trim last char
        out = out.slice(0, -1);
    }

    return out;
}

function _isLikelyPdf(buf) {
    try {
        if (!buf || buf.length < 1000) return false;
        const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
        // PDF files start with bytes: 0x25 0x50 0x44 0x46 0x2D => "%PDF-"
        return b.length >= 5 && b[0] === 0x25 && b[1] === 0x50 && b[2] === 0x44 && b[3] === 0x46 && b[4] === 0x2d;
    } catch {
        return false;
    }
}

// Initialiser le client WhatsApp
const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: './whatsapp-session'
    }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    }
});

// Événements WhatsApp
client.on('qr', (qr) => {
    console.log('QR Code reçu, scannez-le avec WhatsApp:');
    qrcode.generate(qr, { small: true });
    qrCodeData = qr;
});

client.on('ready', () => {
    console.log('✅ WhatsApp client prêt!');
    clientReady = true;
    qrCodeData = null;
});

client.on('authenticated', () => {
    console.log('✅ Authentification réussie!');
});

client.on('auth_failure', (msg) => {
    console.error('❌ Échec d\'authentification:', msg);
    clientReady = false;
});

client.on('disconnected', (reason) => {
    console.log('❌ Déconnecté:', reason);
    clientReady = false;
});

function _cleanupChromiumSingletonLocks(rootDir) {
    try {
        if (!fs.existsSync(rootDir)) return;

        const stack = [rootDir];
        while (stack.length) {
            const dir = stack.pop();
            let entries = [];
            try {
                entries = fs.readdirSync(dir, { withFileTypes: true });
            } catch {
                continue;
            }

            for (const ent of entries) {
                const full = path.join(dir, ent.name);
                if (ent.isDirectory()) {
                    stack.push(full);
                    continue;
                }
                // Chromium lock files that prevent relaunch after crash
                if (ent.name.startsWith('Singleton')) {
                    try {
                        fs.rmSync(full, { force: true });
                    } catch {
                        // ignore
                    }
                }
            }
        }
    } catch {
        // ignore
    }
}

// Démarrer le client
_cleanupChromiumSingletonLocks(path.resolve('./whatsapp-session'));
client.initialize();

// API Endpoints

// Statut du service
app.get('/api/status', (req, res) => {
    res.json({
        status: clientReady ? 'ready' : 'not_ready',
        qrCode: qrCodeData ? true : false
    });
});

// Obtenir le QR code
app.get('/api/qr', (req, res) => {
    if (clientReady) {
        return res.json({ status: 'already_connected' });
    }
    if (qrCodeData) {
        return res.json({ qr: qrCodeData });
    }
    res.json({ status: 'waiting_for_qr' });
});

// Envoyer un message texte
app.post('/api/sendText', async (req, res) => {
    try {
        const { phone, text } = req.body;
        
        if (!clientReady) {
            return res.status(503).json({ error: 'WhatsApp non connecté' });
        }
        
        if (!phone || !text) {
            return res.status(400).json({ error: 'phone et text requis' });
        }
        
        // Formater le numéro
        let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
        if (!chatId.endsWith('@c.us')) {
            chatId = chatId + '@c.us';
        }
        
        const result = await client.sendMessage(chatId, text, { sendSeen: false });
        res.json({ success: true, messageId: result.id._serialized });
        
    } catch (error) {
        console.error('Erreur envoi texte:', error);
        res.status(500).json({ error: error.message });
    }
});

// Envoyer un fichier depuis une URL
app.post('/api/sendFile', async (req, res) => {
    try {
        const { phone, fileUrl, filename, caption } = req.body;
        
        if (!clientReady) {
            return res.status(503).json({ error: 'WhatsApp non connecté' });
        }
        
        if (!phone || !fileUrl) {
            return res.status(400).json({ error: 'phone et fileUrl requis' });
        }
        
        // Formater le numéro
        let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
        if (!chatId.endsWith('@c.us')) {
            chatId = chatId + '@c.us';
        }
        
        console.log(`Téléchargement du fichier depuis: ${fileUrl}`);
        
        // Télécharger le fichier
        const response = await axios.get(fileUrl, { 
            responseType: 'arraybuffer',
            timeout: 30000
        });
        
        const base64Data = Buffer.from(response.data).toString('base64');
        const mimeType = response.headers['content-type'] || 'application/octet-stream';

        const base64Clean = _normalizeBase64(base64Data);
        
        // Créer le média
        const media = new MessageMedia(mimeType, base64Clean, filename || 'document');
        
        // Envoyer
        const result = await client.sendMessage(chatId, media, { caption: caption || '', sendSeen: false, sendMediaAsDocument: true });
        
        res.json({ success: true, messageId: result.id._serialized });
        
    } catch (error) {
        console.error('Erreur envoi fichier:', error);
        res.status(500).json({ error: error.message });
    }
});

// Envoyer un PDF généré depuis une URL HTML
app.post('/api/sendPdf', async (req, res) => {
    try {
        const { phone, htmlUrl, filename, caption } = req.body;
        
        if (!clientReady) {
            return res.status(503).json({ error: 'WhatsApp non connecté' });
        }
        
        if (!phone || !htmlUrl) {
            return res.status(400).json({ error: 'phone et htmlUrl requis' });
        }
        
        // Formater le numéro
        let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
        if (!chatId.endsWith('@c.us')) {
            chatId = chatId + '@c.us';
        }
        
        console.log(`Génération PDF depuis: ${htmlUrl}`);
        
        // Utiliser Puppeteer pour générer le PDF
        const puppeteer = require('puppeteer');
        const browser = await puppeteer.launch({
            headless: true,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu'
            ],
            executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium'
        });
        
        const page = await browser.newPage();
        
        // Définir la viewport pour A4 (210mm x 297mm à 96 DPI)
        await page.setViewport({ width: 794, height: 1123 });
        
        await page.goto(htmlUrl, { waitUntil: 'networkidle0', timeout: 30000 });
        
        // Émuler le media print pour cacher les éléments .no-print
        await page.emulateMediaType('print');
        
        // Générer le PDF au format A4
        const pdfBuffer = await page.pdf({
            format: 'A4',
            printBackground: true,
            preferCSSPageSize: false,
            margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' }
        });
        
        await browser.close();
        
        console.log(`PDF généré: ${pdfBuffer.length} bytes`);

        if (!_isLikelyPdf(pdfBuffer)) {
            throw new Error('PDF généré invalide ou vide (la page source n\'est peut-être pas accessible depuis le conteneur)');
        }
        
        const pdfBuf = Buffer.isBuffer(pdfBuffer) ? pdfBuffer : Buffer.from(pdfBuffer);

        // Convertir en base64
        const base64Data = pdfBuf.toString('base64');
        const base64Clean = _normalizeBase64(base64Data);
        
        // Créer le média
        const media = new MessageMedia('application/pdf', base64Clean, filename || 'document.pdf');
        
        // Envoyer
        const result = await client.sendMessage(chatId, media, { caption: caption || '', sendSeen: false, sendMediaAsDocument: true });
        
        res.json({ success: true, messageId: result.id._serialized });
        
    } catch (error) {
        console.error('Erreur génération/envoi PDF:', error);
        res.status(500).json({ error: error.message });
    }
});

// Générer un PDF depuis une URL HTML (sans envoyer par WhatsApp)
app.post('/api/generatePdf', async (req, res) => {
    try {
        const { htmlUrl, filename } = req.body;
        
        if (!htmlUrl) {
            return res.status(400).json({ error: 'htmlUrl requis' });
        }
        
        console.log(`Génération PDF depuis: ${htmlUrl}`);
        
        // Utiliser Puppeteer pour générer le PDF
        const puppeteer = require('puppeteer');
        const browser = await puppeteer.launch({
            headless: true,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu'
            ],
            executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium'
        });
        
        const page = await browser.newPage();
        
        // Définir la viewport pour A4 (210mm x 297mm à 96 DPI)
        await page.setViewport({ width: 794, height: 1123 });
        
        await page.goto(htmlUrl, { waitUntil: 'networkidle0', timeout: 30000 });
        
        // Émuler le media print pour cacher les éléments .no-print
        await page.emulateMediaType('print');
        
        // Générer le PDF au format A4
        const pdfBuffer = await page.pdf({
            format: 'A4',
            printBackground: true,
            preferCSSPageSize: false,
            margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' }
        });
        
        await browser.close();
        
        console.log(`PDF généré: ${pdfBuffer.length} bytes`);
        
        // Retourner le PDF
        res.setHeader('Content-Type', 'application/pdf');
        res.setHeader('Content-Disposition', `attachment; filename="${filename || 'document.pdf'}"`);
        res.send(pdfBuffer);
        
    } catch (error) {
        console.error('Erreur génération PDF:', error);
        res.status(500).json({ error: error.message });
    }
});

// Envoyer une image depuis une URL
app.post('/api/sendImage', async (req, res) => {
    try {
        const { phone, imageUrl, caption } = req.body;
        
        if (!clientReady) {
            return res.status(503).json({ error: 'WhatsApp non connecté' });
        }
        
        if (!phone || !imageUrl) {
            return res.status(400).json({ error: 'phone et imageUrl requis' });
        }
        
        // Formater le numéro
        let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
        if (!chatId.endsWith('@c.us')) {
            chatId = chatId + '@c.us';
        }
        
        // Télécharger l'image
        const media = await MessageMedia.fromUrl(imageUrl);

        if (media && media.data) {
            media.data = _normalizeBase64(media.data);
        }
        
        // Envoyer
        const result = await client.sendMessage(chatId, media, { caption: caption || '', sendSeen: false, sendMediaAsDocument: true });
        
        res.json({ success: true, messageId: result.id._serialized });
        
    } catch (error) {
        console.error('Erreur envoi image:', error);
        res.status(500).json({ error: error.message });
    }
});

// Démarrer le serveur
app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Service WhatsApp démarré sur le port ${PORT}`);
    console.log(`📱 Endpoints disponibles:`);
    console.log(`   GET  /api/status - Statut du service`);
    console.log(`   GET  /api/qr - Obtenir le QR code`);
    console.log(`   POST /api/sendText - Envoyer un message`);
    console.log(`   POST /api/sendFile - Envoyer un fichier`);
    console.log(`   POST /api/sendImage - Envoyer une image`);
});
