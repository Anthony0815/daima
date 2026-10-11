# 微积分题库与考试模拟系统 —— 容器镜像
#
# 本项目零第三方依赖，所以没有 pip install 这一步，镜像很小、构建很快。
#
# 注意：只 COPY server/ 与 web/，**刻意不拷贝 llm.env**。
# 否则密钥会被烧进镜像层，任何拿到镜像的人都能提取出来。
# 密钥请在平台的「环境变量」里配置。

FROM python:3.12-slim

WORKDIR /app

COPY server/ ./server/
COPY web/ ./web/
COPY tools/ ./tools/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CALCULUS_DB=/data/app.db

# 数据目录。挂载持久卷到 /data 才能保住账号与成绩；不挂载则每次重启清空。
RUN mkdir -p /data

# 平台会通过 PORT 环境变量注入端口，程序会自动改听 0.0.0.0
EXPOSE 8788

CMD ["python", "server/app.py"]
