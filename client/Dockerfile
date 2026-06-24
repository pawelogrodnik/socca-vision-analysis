FROM node:20.19-alpine

WORKDIR /app

# Copy only dependency metadata first so Docker can cache npm install.
# package-lock.json is intentionally not committed in this starter because
# generated lockfiles can contain environment-specific registry URLs.
COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
