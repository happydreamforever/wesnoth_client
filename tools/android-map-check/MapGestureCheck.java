package org.wesnoth.mapcheck;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.File;
import java.io.FileOutputStream;

/** Test-only companion. Does not authenticate, navigate, or read game storage. */
public final class MapGestureCheck extends Instrumentation {
    private Bundle arguments;
    private UiAutomation automation;
    private long down;
    private int width, height;
    @Override public void onCreate(Bundle args) { arguments=args; start(); }
    private void foreground() {
        AccessibilityNodeInfo root=automation.getRootInActiveWindow();
        if(root==null || !"org.wesnoth.offline".contentEquals(root.getPackageName()))
            throw new IllegalStateException("Keep the game map in the foreground.");
        root.recycle();
    }
    private void event(int action, int count, float x1, float y1, float x2, float y2) {
        MotionEvent.PointerProperties[] properties=new MotionEvent.PointerProperties[count];
        MotionEvent.PointerCoords[] positions=new MotionEvent.PointerCoords[count];
        for(int n=0;n<count;n++) {
            properties[n]=new MotionEvent.PointerProperties(); properties[n].id=count==1?9:(n==0?3:9);
            properties[n].toolType=MotionEvent.TOOL_TYPE_FINGER;
            positions[n]=new MotionEvent.PointerCoords(); positions[n].x=(n==0?x1:x2)*width;
            positions[n].y=(n==0?y1:y2)*height;positions[n].pressure=1;positions[n].size=.04f;
        }
        // Initial single pointer has ID 3; after POINTER_UP, ID 9 continues.
        if(action==MotionEvent.ACTION_DOWN)properties[0].id=3;
        MotionEvent e=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,count,properties,positions,0,0,1,1,0,0,InputDevice.SOURCE_TOUCHSCREEN,0);
        try {if(!automation.injectInputEvent(e,true))throw new IllegalStateException("Touch injection failed.");}
        finally {e.recycle();}
        SystemClock.sleep(16);
    }
    private void capture(File folder,String name) throws Exception {
        Bitmap image=automation.takeScreenshot();if(image==null)throw new IllegalStateException("Screenshot unavailable.");
        try(FileOutputStream file=new FileOutputStream(new File(folder,name))) {image.compress(Bitmap.CompressFormat.PNG,100,file);}finally{image.recycle();}
    }
    @Override public void onStart() {
        Bundle result=new Bundle(); boolean touched=false;float x=.43f,y=.40f;
        try {
            String screen=arguments.getString("screen","");
            if(!screen.equals("world")&&!screen.equals("area"))throw new IllegalArgumentException("Open World or Castle map first; specify screen.");
            automation=getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);foreground();
            Bitmap initial=automation.takeScreenshot();if(initial==null)throw new IllegalStateException("Screenshot unavailable.");
            width=initial.getWidth();height=initial.getHeight();initial.recycle();if(width<=height)throw new IllegalStateException("Landscape game map required.");
            File folder=new File(getTargetContext().getExternalFilesDir(null),"map-check-"+screen+"-"+System.currentTimeMillis());
            if(!folder.mkdirs())throw new IllegalStateException("Could not retain diagnostic images.");
            capture(folder,"before.png");down=SystemClock.uptimeMillis();touched=true;
            event(MotionEvent.ACTION_DOWN,1,.43f,y,0,0);
            event(MotionEvent.ACTION_POINTER_DOWN|(1<<MotionEvent.ACTION_POINTER_INDEX_SHIFT),2,.43f,y,.57f,y);
            for(int i=1;i<=25;i++)event(MotionEvent.ACTION_MOVE,2,.43f-i*.005f,y+i*.001f,.57f+i*.005f,y+i*.001f);
            x=.695f;y=.425f;
            event(MotionEvent.ACTION_POINTER_UP,2,.305f,y,x,y);
            for(int i=1;i<=25;i++)event(MotionEvent.ACTION_MOVE,1,x+i*.002f,y+i*.001f,0,0);
            x+=.05f;y+=.025f;event(MotionEvent.ACTION_UP,1,x,y,0,0);touched=false;
            SystemClock.sleep(400);foreground();capture(folder,"after.png");
            result.putString("stream","MOBILE MAP GESTURES SENT; VISUAL REVIEW REQUIRED\nImages: "+folder.getAbsolutePath()+"\nSynthetic two-pointer input, not a physical touchscreen test.\n");finish(-1,result);
        } catch(Exception error) {
            if(touched)try{event(MotionEvent.ACTION_CANCEL,1,x,y,0,0);}catch(Exception ignored){}
            result.putString("stream","MAP CHECK STOPPED: "+error.getMessage()+"\n");finish(1,result);
        }
    }
}
