// Squircle Paint Worklet - 超椭圆形 (Superellipse) 的正确实现
// 基于 iOS 风格的 squircle 算法
// 关键：使用 sign() 函数处理四个象限，确保路径连续不穿过内部

class SquirclePainter {
  static get inputProperties() {
    return ['--squircle-radius', '--squircle-smooth'];
  }

  paint(ctx, size, properties) {
    const width = size.width;
    const height = size.height;
    const radiusStr = properties.get('--squircle-radius').toString().trim();
    const smoothStr = properties.get('--squircle-smooth').toString().trim();

    // 解析 CSS 变量
    let radius = parseFloat(radiusStr);
    let smooth = parseFloat(smoothStr);

    // 默认值
    if (isNaN(radius)) radius = 20;
    if (isNaN(smooth)) smooth = 0.9;

    // 将百分比转换为像素
    if (radiusStr.includes('%')) {
      radius = (Math.min(width, height) / 2) * (radius / 100);
    }

    // 确保 radius 不超过容器的一半
    radius = Math.min(radius, Math.min(width, height) / 2);

    // smooth 值应该在 0.5 到 1 之间
    smooth = Math.max(0.5, Math.min(1, smooth));

    // 绘制 squircle 路径
    this.drawSquircle(ctx, width, height, radius, smooth);

    // 填充白色（用于 mask-image）
    ctx.fillStyle = 'white';
    ctx.fill();
  }

  // 符号函数
  sign(x) {
    return x === 0 ? 0 : x > 0 ? 1 : -1;
  }

  drawSquircle(ctx, width, height, radius, smooth) {
    ctx.beginPath();

    // 特殊情况：radius = 0 时画矩形（没有圆角）
    if (radius === 0) {
      ctx.rect(0, 0, width, height);
      ctx.closePath();
      return;
    }

    // Squircle 公式（iOS 风格）：
    // x = centerX + (halfWidth - radius) * cos^(2/p) θ * sign(cos θ) + radius * sign(cos θ)
    // y = centerY + (halfHeight - radius) * sin^(2/p) θ * sign(sin θ) + radius * sign(sin θ)
    // 其中 p 是曲率参数（p=1 时最圆，p>1 时更方）

    const centerX = width / 2;
    const centerY = height / 2;
    const halfWidth = width / 2;
    const halfHeight = height / 2;
    
    // 曲率指数
    const pow = smooth;
    
    // 总段数（对于完整圆周）
    const totalSegments = 200;
    
    for (let i = 0; i <= totalSegments; i++) {
      // 参数 t 从 0 到 2π
      const t = (i / totalSegments) * Math.PI * 2;
      const cos_t = Math.cos(t);
      const sin_t = Math.sin(t);
      
      // 计算 cos^(2/p) 和 sin^(2/p)，保留符号
      const cos_pow = Math.pow(Math.abs(cos_t), 2 / pow) * this.sign(cos_t);
      const sin_pow = Math.pow(Math.abs(sin_t), 2 / pow) * this.sign(sin_t);
      
      // 计算坐标
      const x = centerX + (halfWidth - radius) * cos_pow + radius * this.sign(cos_t);
      const y = centerY + (halfHeight - radius) * sin_pow + radius * this.sign(sin_t);
      
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }

    // 闭合路径
    ctx.closePath();
  }
}

registerPaint('squircle', SquirclePainter);
