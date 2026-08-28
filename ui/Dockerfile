# 构建阶段
FROM node:18-alpine AS build-stage

# 设置 npm 镜像源以加速下载
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app

COPY package*.json ./
# 利用 Docker 缓存，只有 package.json 改变时才重新 install
RUN npm install

COPY . .
RUN npm run build

# 生产阶段
FROM nginx:stable-alpine AS production-stage

COPY --from=build-stage /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
