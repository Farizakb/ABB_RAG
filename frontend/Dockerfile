# frontend/Dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# nginx:alpine's worker processes already run as the unprivileged `nginx`
# user by default (see /etc/nginx/nginx.conf's `user nginx;`); only the
# master process, which needs to bind port 80, stays root.
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
