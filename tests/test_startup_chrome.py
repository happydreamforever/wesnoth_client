"""Execute the production Drawable against a recording Canvas; no device visual claim."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUBS = {
    'Rect': 'public int left,top,right,bottom; public int width(){return right-left;} public int height(){return bottom-top;} public boolean isEmpty(){return width()<=0||height()<=0;}',
    'ColorFilter': '',
    'PixelFormat': 'public static final int TRANSLUCENT=-3;',
    'Shader': 'public enum TileMode {CLAMP}',
    'LinearGradient': 'public LinearGradient(float a,float b,float c,float d,int e,int f,Shader.TileMode g){}',
    'Paint': '''public static final int ANTI_ALIAS_FLAG=1; public enum Style {FILL,STROKE}
 public int color,alpha; public Style style; public Shader shader; public Paint(int flags){}
 public void setShader(Shader s){shader=s;} public void setColor(int c){color=c;}
 public void setAlpha(int a){alpha=a;} public void setStyle(Style s){style=s;}
 public void setStrokeWidth(float w){} public void setColorFilter(ColorFilter f){}''',
    'Path': '''private void finite(float... args){for(float v:args)if(!Float.isFinite(v))throw new AssertionError("nonfinite path");}
 public void moveTo(float x,float y){finite(x,y);} public void lineTo(float x,float y){finite(x,y);}
 public void cubicTo(float a,float b,float c,float d,float e,float f){finite(a,b,c,d,e,f);} public void close(){}''',
    'Canvas': '''public int paths,rects,depth,firstPathFill; public float left,top,right,bottom; public int fill,alpha; public Shader shader;
 public int save(){return ++depth;} public void clipRect(Rect r){} public void translate(float x,float y){}
 public void scale(float x,float y){} public void rotate(float degrees){} public void restoreToCount(int n){depth=n-1;}
 public void drawPath(Path p,Paint paint){if(paths==0)firstPathFill=paint.color;paths++;} public void drawLine(float a,float b,float c,float d,Paint p){}
 public void drawRect(float l,float t,float r,float b,Paint p){rects++;left=l;top=t;right=r;bottom=b;fill=p.color;alpha=p.alpha;shader=p.shader;}''',
}
DRAWABLE = '''package android.graphics.drawable;
import android.graphics.*;
public abstract class Drawable {
 private final Rect bounds=new Rect(); private int level;
 public Rect getBounds(){return bounds;} public void setBounds(int l,int t,int r,int b){bounds.left=l;bounds.top=t;bounds.right=r;bounds.bottom=b;}
 public int getLevel(){return level;} public boolean setLevel(int l){level=l;return onLevelChange(l);}
 protected boolean onLevelChange(int l){return false;} public void invalidateSelf(){}
 public boolean isStateful(){return false;} protected boolean onStateChange(int[] state){return false;}
 public boolean setState(int[] state){return onStateChange(state);}
 public abstract void draw(Canvas c); public abstract void setAlpha(int a); public abstract void setColorFilter(ColorFilter f); public abstract int getOpacity();
}'''


class StartupChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='startup-chrome-')
        cls.addClassCleanup(cls.temp.cleanup); cls.root = Path(cls.temp.name)
        graphics = cls.root / 'android/graphics'; graphics.mkdir(parents=True)
        (cls.root / 'android/R.java').write_text('package android; public final class R {public static class attr {public static final int state_pressed=1,state_focused=2;}}')
        for name, body in STUBS.items():
            inherit = ' extends Shader' if name == 'LinearGradient' else ''
            (graphics / (name + '.java')).write_text('package android.graphics; public class ' + name + inherit + ' {' + body + '}')
        (graphics / 'drawable').mkdir(); (graphics / 'drawable/Drawable.java').write_text(DRAWABLE)
        source = cls.root / 'org/wesnoth/offline'; source.mkdir(parents=True)
        for name in ('StartupFrame', 'ProductTheme'):
            shutil.copyfile(ROOT / 'tools/native-account-game' / (name + '.java'), source / (name + '.java'))
        (source / 'ChromeHarness.java').write_text('''package org.wesnoth.offline;
import android.graphics.*;
public final class ChromeHarness {
 public static void main(String[] args) {
  float density=Float.parseFloat(args[0]);
  for(int value:new int[]{-10,0,1,5000,9900,10000,15000}) {
   StartupFrame meter=new StartupFrame(density,false,false,true);
   meter.setBounds(23,47,23+(int)(200*density),47+(int)(28*density)); meter.setLevel(value);
   Canvas c=new Canvas(); meter.draw(c);
   if(c.depth!=0)throw new AssertionError("unbalanced canvas");
   if(value<=0) {if(c.rects!=0)throw new AssertionError("empty work painted");continue;}
   float expected=184*Math.min(value,10000)/10000f;
   if(c.rects!=1 || Math.abs(c.right-c.left-expected)>.001 || c.top!=8 || c.bottom!=20 || c.left!=8)
    throw new AssertionError("measured fill or inset changed: "+value);
   if(c.shader==null||c.fill!=ProductTheme.GOLD)throw new AssertionError("shared gradient missing");
  }
  StartupFrame empty=new StartupFrame(density,false,true,false);Canvas e=new Canvas();empty.draw(e);
  if(e.paths!=0||e.rects!=0)throw new AssertionError("empty bounds painted");
  StartupFrame panel=new StartupFrame(density,false,true,false);panel.setBounds(0,0,720,250);
  Canvas decorated=new Canvas();panel.draw(decorated);
  StartupFrame plain=new StartupFrame(density,true,false,false);plain.setBounds(0,0,720,250);
  Canvas undecorated=new Canvas();plain.draw(undecorated);
  if(decorated.paths<=undecorated.paths || decorated.rects!=0 || decorated.depth!=0)
   throw new AssertionError("ornaments absent or canvas leaked");
  if(!plain.setState(new int[]{android.R.attr.state_pressed}))throw new AssertionError("press ignored");
  Canvas pressed=new Canvas();plain.draw(pressed);
  if(pressed.firstPathFill==undecorated.firstPathFill)throw new AssertionError("no press feedback");
  plain.setState(new int[]{});Canvas released=new Canvas();plain.draw(released);
  if(released.firstPathFill!=undecorated.firstPathFill)throw new AssertionError("release not restored");
  StartupFrame faded=new StartupFrame(density,false,false,true);faded.setBounds(0,0,(int)(200*density),(int)(28*density));faded.setLevel(5000);faded.setAlpha(80);
  Canvas f=new Canvas();faded.draw(f);if(f.alpha!=80)throw new AssertionError("alpha ignored");
  System.out.println("MEASURED EMERALD DRAWABLE PASS density="+density);
 }
}''')
        subprocess.run(['javac', '--release', '8', '-d', str(cls.root / 'classes'), *map(str, cls.root.rglob('*.java'))],
                       check=True, capture_output=True, timeout=30)

    def test_actual_meter_zero_partial_complete_and_bounds_at_multiple_densities(self):
        for density in (1, 1.5, 2, 3):
            with self.subTest(density=density):
                result = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'org.wesnoth.offline.ChromeHarness', str(density)],
                                        check=True, capture_output=True, text=True, timeout=30)
                self.assertIn('MEASURED EMERALD DRAWABLE PASS', result.stdout)


if __name__ == '__main__': unittest.main()
