FROM node:18-slim

# Installer les dépendances pour Puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    dbus \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/dbus

# Variables d'environnement pour Puppeteer
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Copier les fichiers
COPY package.json ./
RUN npm install

COPY index.js ./

# Créer le dossier pour la session
RUN mkdir -p /app/whatsapp-session

EXPOSE 3001

CMD ["node", "index.js"]
