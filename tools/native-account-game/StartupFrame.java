package org.wesnoth.offline;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/** APK-local Emerald Court chrome; usable before the game data is installed. */
final class StartupFrame extends Drawable {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float density;
    private final boolean parchment;
    private final boolean ornaments;
    private final boolean meter;
    private int opacity = 255;
    private boolean active;

    StartupFrame(float density, boolean parchment, boolean ornaments, boolean meter) {
        this.density = density;
        this.parchment = parchment;
        this.ornaments = ornaments;
        this.meter = meter;
    }

    private Path outline(float width, float height, float inset) {
        float cut = Math.max(0, Math.min(10, Math.min(width, height) / 4 - inset));
        Path path = new Path();
        path.moveTo(inset + cut, inset); path.lineTo(width - inset - cut, inset);
        path.lineTo(width - inset, inset + cut); path.lineTo(width - inset, height - inset - cut);
        path.lineTo(width - inset - cut, height - inset); path.lineTo(inset + cut, height - inset);
        path.lineTo(inset, height - inset - cut); path.lineTo(inset, inset + cut); path.close();
        return path;
    }

    private void color(int value, Paint.Style style, float width) {
        paint.setShader(null); paint.setColor(value); paint.setAlpha(opacity);
        paint.setStyle(style); paint.setStrokeWidth(width);
    }

    @Override public void draw(Canvas canvas) {
        if (getBounds().isEmpty()) return;
        int saved = canvas.save();
        canvas.clipRect(getBounds());
        canvas.translate(getBounds().left, getBounds().top); canvas.scale(density, density);
        float width = getBounds().width() / density, height = getBounds().height() / density;
        color(parchment ? (active ? ProductTheme.LIGHT_GOLD : ProductTheme.PARCHMENT)
                        : (active ? ProductTheme.TEAL_SHADOW : ProductTheme.STONE), Paint.Style.FILL, 0);
        canvas.drawPath(outline(width, height, 2), paint);
        if (meter) {
            // ProgressBar supplies its measured 0..10000 level. No animation or estimated work.
            float fraction = Math.max(0, Math.min(10000, getLevel())) / 10000f;
            if (fraction > 0 && width > 16 && height > 16) {
                color(ProductTheme.GOLD, Paint.Style.FILL, 0);
                paint.setShader(new LinearGradient(0, 8, 0, height - 8,
                        ProductTheme.LIGHT_GOLD, ProductTheme.GOLD, Shader.TileMode.CLAMP));
                canvas.drawRect(8, 8, 8 + (width - 16) * fraction, height - 8, paint);
            }
        }
        color(ProductTheme.INK, Paint.Style.STROKE, 4); canvas.drawPath(outline(width, height, 2), paint);
        color(ProductTheme.DARK_GOLD, Paint.Style.STROKE, 2.6f); canvas.drawPath(outline(width, height, 2), paint);
        color(ProductTheme.LIGHT_GOLD, Paint.Style.STROKE, .9f); canvas.drawPath(outline(width, height, 3.5f), paint);
        color(ProductTheme.GOLD, Paint.Style.STROKE, .8f); canvas.drawPath(outline(width, height, 6), paint);
        if (ornaments) {
            float size = Math.min(54, Math.min(width, height) / 3);
            corner(canvas, 0, 0, size, 0); corner(canvas, width, 0, size, 90);
            corner(canvas, width, height, size, 180); corner(canvas, 0, height, size, 270);
        }
        canvas.restoreToCount(saved);
    }

    private void corner(Canvas canvas, float x, float y, float size, float rotation) {
        int saved = canvas.save(); canvas.translate(x, y); canvas.rotate(rotation);
        canvas.translate(size * .37f, size * .28f); canvas.scale(size / 100, size / 100);
        canvas.translate(-18, -12); canvas.rotate((float) Math.toDegrees(-.72));
        leaf(canvas, 50, 67, 42, 0);
        leaf(canvas, 45, 76, 33, -.65f); leaf(canvas, 55, 76, 33, .65f);
        leaf(canvas, 38, 82, 27, -1.02f); leaf(canvas, 62, 82, 27, 1.02f);
        leaf(canvas, 31, 85, 20, -1.35f); leaf(canvas, 69, 85, 20, 1.35f);
        Path stem = new Path(); stem.moveTo(50, 94); stem.cubicTo(50, 79, 50, 66, 50, 40);
        stem.moveTo(50, 91); stem.cubicTo(39, 86, 31, 80, 22, 70);
        stem.moveTo(50, 91); stem.cubicTo(61, 86, 69, 80, 78, 70);
        color(ProductTheme.LIGHT_GOLD, Paint.Style.STROKE, 1.6f); canvas.drawPath(stem, paint);
        canvas.restoreToCount(saved);
    }

    private void leaf(Canvas canvas, float x, float y, float length, float angle) {
        int saved = canvas.save(); canvas.translate(x, y); canvas.rotate((float) Math.toDegrees(angle));
        canvas.scale(length / 30, length / 30);
        Path shape = new Path(); shape.moveTo(0, 0);
        shape.cubicTo(-3, -4, -9, -6, -6, -10); shape.cubicTo(-11, -13, -9, -17, -5, -18);
        shape.cubicTo(-8, -23, -3, -25, 0, -30); shape.cubicTo(3, -25, 8, -23, 5, -18);
        shape.cubicTo(9, -17, 11, -13, 6, -10); shape.cubicTo(9, -6, 3, -4, 0, 0); shape.close();
        color(ProductTheme.GOLD, Paint.Style.FILL, 0);
        paint.setShader(new LinearGradient(-8, -20, 8, 0, ProductTheme.LIGHT_GOLD,
                ProductTheme.DARK_GOLD, Shader.TileMode.CLAMP)); canvas.drawPath(shape, paint);
        color(ProductTheme.GOLD, Paint.Style.STROKE, .65f); canvas.drawPath(shape, paint);
        color(ProductTheme.IVORY, Paint.Style.STROKE, .45f); canvas.drawLine(0, -2, 0, -25, paint);
        canvas.restoreToCount(saved);
    }

    @Override protected boolean onLevelChange(int level) { invalidateSelf(); return true; }
    @Override public boolean isStateful() { return true; }
    @Override protected boolean onStateChange(int[] state) {
        boolean next = false;
        for (int value : state) {
            if (value == android.R.attr.state_pressed || value == android.R.attr.state_focused) next = true;
        }
        if (active == next) return false;
        active = next; invalidateSelf(); return true;
    }
    @Override public void setAlpha(int alpha) { opacity = Math.max(0, Math.min(255, alpha)); invalidateSelf(); }
    @Override public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); invalidateSelf(); }
    @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
