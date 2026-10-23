"""Execute the owned keyboard/task adapters against bounded Android API doubles.

This checks state transitions and API calls, not LDPlayer rendering or an Android
input method. The normal offline APK build compiles against the real SDK as well.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'tools/native-account-game'
STUBS = {
'android/text/InputType.java': '''package android.text; public final class InputType {
public static final int TYPE_MASK_CLASS=15,TYPE_CLASS_TEXT=1,TYPE_TEXT_FLAG_CAP_CHARACTERS=4096,
TYPE_TEXT_FLAG_CAP_WORDS=8192,TYPE_TEXT_FLAG_CAP_SENTENCES=16384,TYPE_TEXT_FLAG_AUTO_CORRECT=32768,
TYPE_TEXT_FLAG_AUTO_COMPLETE=65536,TYPE_TEXT_FLAG_NO_SUGGESTIONS=524288;}''',
'android/view/InputDevice.java': '''package android.view; public class InputDevice {
public static final int SOURCE_UNKNOWN=0,SOURCE_KEYBOARD=257; public static InputDevice device;
public static InputDevice getDevice(int id){return device;} public int getSources(){return SOURCE_KEYBOARD;}}''',
'android/view/KeyEvent.java': '''package android.view; public class KeyEvent {
public static final int ACTION_DOWN=0,ACTION_UP=1; final long down,time; final int action,key,repeat,meta,device,scan,flags,source;
public KeyEvent(long d,long t,int a,int k,int r,int m,int dev,int sc,int f,int so){down=d;time=t;action=a;key=k;repeat=r;meta=m;device=dev;scan=sc;flags=f;source=so;}
public long getDownTime(){return down;} public long getEventTime(){return time;}
public int getAction(){return action;} public int getKeyCode(){return key;} public int getRepeatCount(){return repeat;}
public int getMetaState(){return meta;} public int getDeviceId(){return device;} public int getScanCode(){return scan;}
public int getFlags(){return flags;} public int getSource(){return source;}
public int getUnicodeChar(){boolean shift=(meta&193)!=0, caps=(meta&1048576)!=0;
if(key==29)return shift!=caps?'A':'a'; if(key==8)return shift?'!':'1'; if(key==62)return ' '; return 0;}}''',
'org/libsdl/app/SDLActivity.java': '''package org.libsdl.app; import java.util.*; public class SDLActivity {
public static final List<String> calls=new ArrayList<>();
public static void onNativeKeyDown(int key){calls.add("down:"+key);} public static void onNativeKeyUp(int key){calls.add("up:"+key);}}''',
'android/content/Context.java': '''package android.content; public class Context { public static final String ACTIVITY_SERVICE="activity";}''',
'android/os/Looper.java': '''package android.os; public class Looper { private static final Looper MAIN=new Looper(); public static boolean main;
public static Looper myLooper(){return main?MAIN:null;} public static Looper getMainLooper(){return MAIN;}}''',
'android/app/ActivityManager.java': '''package android.app; import java.util.*; public class ActivityManager {
public final List<AppTask> tasks=new ArrayList<>(); public List<AppTask> getAppTasks(){return tasks;}
public static class RecentTaskInfo {public int id;}
public static class AppTask {public final RecentTaskInfo info=new RecentTaskInfo();public int removed;public boolean fail;
public AppTask(int id){info.id=id;}public RecentTaskInfo getTaskInfo(){return info;}
public void finishAndRemoveTask(){if(fail)throw new IllegalStateException("PRIVATE SENTINEL");removed++;}}}''',
'android/app/Activity.java': '''package android.app; import android.content.Context; public class Activity extends Context {
public int id=42,affinity,removed; public boolean delayed,failPost; public Runnable pending;
public ActivityManager manager=new ActivityManager(); public int getTaskId(){return id;}
public Object getSystemService(String name){return manager;}
public void runOnUiThread(Runnable task){if(failPost)throw new IllegalStateException("PRIVATE SENTINEL");if(delayed)pending=task;else task.run();}
public void finishAffinity(){affinity++;}public void finishAndRemoveTask(){removed++;}}''',
}

KEYBOARD_HARNESS = r'''package org.libsdl.app;
import android.view.*; import android.text.InputType;
public final class KeyboardHarness {
 static void yes(boolean condition){if(!condition)throw new AssertionError("Keyboard behavior mismatch");}
 static KeyEvent key(int code,int action,int meta){return new KeyEvent(11,22,action,code,0,meta,1,33,44,257);}
 static void fresh(){WesnothKeyboard.focusLost();SDLActivity.calls.clear();}
 public static void main(String[] args){
  String test=args[0];
  if(test.equals("input_type")){
   int flags=4096|8192|16384|32768|65536;
   for(int variation:new int[]{0,16,32,64,128,144,224}){
    int value=1|variation|flags|131072;
    yes(WesnothKeyboard.inputType(value)==(1|variation|524288|131072));
   }
   yes(WesnothKeyboard.inputType(2|flags)==(2|flags));
  }else if(test.equals("intentional_case_symbols")){
   fresh(); for(int[] item:new int[][]{{0,'a'},{1,'A'},{1048576,'A'},{1048577,'a'}})
    yes(WesnothKeyboard.event(key(29,0,item[0])).getUnicodeChar()==item[1]);
   yes(WesnothKeyboard.event(key(8,0,1)).getUnicodeChar()=='!');
   yes(WesnothKeyboard.event(key(8,0,0)).getUnicodeChar()=='1');
   yes(WesnothKeyboard.event(key(62,0,0)).getUnicodeChar()==' ');
  }else if(test.equals("missing_release")){
   fresh(); WesnothKeyboard.event(key(59,0,65)); SDLActivity.calls.clear();
   yes(WesnothKeyboard.event(key(29,0,0)).getUnicodeChar()=='a');
   yes(SDLActivity.calls.contains("up:59"));
  }else if(test.equals("stale_after_release")){
   fresh(); WesnothKeyboard.event(key(59,0,65));WesnothKeyboard.event(key(59,1,65));
   yes(WesnothKeyboard.event(key(29,0,65)).getUnicodeChar()=='a');
   WesnothKeyboard.event(key(59,0,65));yes(WesnothKeyboard.event(key(29,0,65)).getUnicodeChar()=='A');
  }else if(test.equals("explicit_reset")){
   fresh();String before="MiXeD!";WesnothKeyboard.event(key(29,0,1048641));WesnothKeyboard.reset();
   yes(WesnothKeyboard.event(key(29,0,1048641)).getUnicodeChar()=='a');yes(before.equals("MiXeD!"));
   WesnothKeyboard.event(key(59,0,65));yes(WesnothKeyboard.event(key(8,0,65)).getUnicodeChar()=='!');
   WesnothKeyboard.event(key(59,1,0));WesnothKeyboard.event(key(115,0,1048576));
   yes(WesnothKeyboard.event(key(29,0,1048576)).getUnicodeChar()=='A');
  }else if(test.equals("reset_release_then_press")){
   WesnothKeyboard.reset();yes(WesnothKeyboard.event(key(29,0,1048576)).getUnicodeChar()=='a');
   yes(WesnothKeyboard.event(key(29,0,0)).getUnicodeChar()=='a');
   yes(WesnothKeyboard.event(key(29,0,1048576)).getUnicodeChar()=='A');
  }else if(test.equals("focus_loss")){
   fresh();WesnothKeyboard.event(key(59,0,65));WesnothKeyboard.focusLost();
   yes(SDLActivity.calls.size()==8);yes(SDLActivity.calls.contains("up:59"));
   yes(WesnothKeyboard.event(key(29,0,1048576)).getUnicodeChar()=='A');
  }else if(test.equals("other_inputs")){
   KeyEvent multi=new KeyEvent(1,2,2,0,0,1048576,1,0,0,257);
   KeyEvent pad=new KeyEvent(1,2,0,29,0,1048576,1,0,0,1025);
   yes(WesnothKeyboard.event(multi)==multi);yes(WesnothKeyboard.event(pad)==pad);
   KeyEvent ctrl=key(29,0,12288);yes(WesnothKeyboard.event(ctrl)==ctrl);
   InputDevice.device=new InputDevice();KeyEvent unknown=new KeyEvent(1,2,0,29,0,0,1,0,0,0);
   yes(WesnothKeyboard.event(unknown)==unknown);
  }else if(test.equals("metadata_preserved")){
   WesnothKeyboard.reset();KeyEvent event=new KeyEvent(11,22,0,29,3,1048576,4,33,44,257);
   KeyEvent actual=WesnothKeyboard.event(event);yes(actual.getUnicodeChar()=='a');
   yes(actual.getDownTime()==11&&actual.getEventTime()==22&&actual.getAction()==0&&actual.getKeyCode()==29);
   yes(actual.getRepeatCount()==3&&actual.getDeviceId()==4&&actual.getScanCode()==33&&actual.getFlags()==44&&actual.getSource()==257);
  }else if(test.equals("multiple_devices_bounded")){
   WesnothKeyboardState state=new WesnothKeyboardState();state.event(1,59,0,0,65);state.event(2,29,0,0,0);yes(state.held()==1);
   state.event(1,59,1,0,0);yes(state.held()==0);
   for(int i=0;i<100;i++)state.event(i,29,0,0,0);yes(state.deviceCount()==8);
  }else if(test.equals("both_shift_sides")){
   WesnothKeyboardState state=new WesnothKeyboardState();state.event(1,59,0,0,65);state.event(1,60,0,0,193);yes(state.held()==3);
   int meta=state.event(1,59,1,0,193);yes((meta&128)!=0&&(meta&64)==0&&state.held()==2);
   state.event(1,60,1,0,129);yes(state.held()==0);
  }else throw new AssertionError("Unknown case");
  System.out.println("PASS "+test);
 }
}'''

TASK_HARNESS = r'''package org.wesnoth.offline;
import android.app.*;import android.os.Looper;
public final class TaskHarness {
 static void yes(boolean value){if(!value)throw new AssertionError("Task close mismatch");}
 public static void main(String[] args){String test=args[0];Activity activity=new Activity();
  ActivityManager.AppTask own=new ActivityManager.AppTask(42),other=new ActivityManager.AppTask(73);
  activity.manager.tasks.add(other);activity.manager.tasks.add(own);
  if(test.equals("native_thread")||test.equals("ui_thread")){
   Looper.main=test.equals("ui_thread");yes(WesnothGameTask.finish(activity));
   yes(own.removed==1&&other.removed==0&&activity.affinity==0&&activity.removed==0);
  }else if(test.equals("fallback")){
   activity.manager.tasks.clear();yes(WesnothGameTask.finish(activity));yes(activity.affinity==1&&activity.removed==1);
  }else if(test.equals("deadline")){
   activity.delayed=true;long start=System.nanoTime();yes(!WesnothGameTask.finish(activity));
   long ms=(System.nanoTime()-start)/1000000;yes(ms>=1400&&ms<3000&&own.removed==0&&other.removed==0);
  }else if(test.equals("interrupted")){
   activity.delayed=true;Thread.currentThread().interrupt();yes(!WesnothGameTask.finish(activity));
   yes(Thread.currentThread().isInterrupted()&&own.removed==0);Thread.interrupted();
  }else if(test.equals("failure")){
   own.fail=true;yes(!WesnothGameTask.finish(activity));yes(other.removed==0);
   own.fail=false;activity.failPost=true;yes(!WesnothGameTask.finish(activity));
  }else throw new AssertionError("Unknown case");
  System.out.println("PASS "+test);
 }
}'''

class AndroidInputLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac = os.environ.get('JAVAC') or shutil.which('javac')
        cls.java = os.environ.get('JAVA') or shutil.which('java')
        if not cls.java or not cls.javac:
            raise RuntimeError('Java compiler/runtime required; run with the pinned Java21 build image.')
        cls.temp = tempfile.TemporaryDirectory(prefix='wesnoth-keyboard-lifecycle-')
        cls.root = Path(cls.temp.name)
        for name, content in STUBS.items():
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for name in ('WesnothKeyboardState.java','WesnothKeyboard.java'):
            shutil.copyfile(SOURCES / name, cls.root / 'org/libsdl/app' / name)
        target=cls.root / 'org/wesnoth/offline'
        target.mkdir(parents=True)
        shutil.copyfile(SOURCES/'WesnothGameTask.java',target/'WesnothGameTask.java')
        (cls.root/'org/libsdl/app/KeyboardHarness.java').write_text(KEYBOARD_HARNESS)
        (target/'TaskHarness.java').write_text(TASK_HARNESS)
        subprocess.run([cls.javac,'--release','8','-d',str(cls.root/'classes'),
                        *map(str,cls.root.rglob('*.java'))],check=True,capture_output=True,text=True,timeout=30)
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def run_case(self, group, case):
        output=subprocess.run([self.java,'-cp',str(self.root/'classes'),group,case],
                              capture_output=True,text=True,timeout=8)
        self.assertEqual(output.returncode,0,output.stdout+output.stderr)
        self.assertEqual(output.stdout.strip(),'PASS '+case)
        self.assertNotIn('PRIVATE SENTINEL',output.stdout+output.stderr)
    def test_keyboard_cases(self):
        for case in ('input_type','intentional_case_symbols','missing_release','stale_after_release',
                     'explicit_reset','reset_release_then_press','focus_loss','other_inputs',
                     'metadata_preserved','multiple_devices_bounded','both_shift_sides'):
            with self.subTest(case=case):self.run_case('org.libsdl.app.KeyboardHarness',case)
    def test_task_cases(self):
        for case in ('native_thread','ui_thread','fallback','deadline','interrupted','failure'):
            with self.subTest(case=case):self.run_case('org.wesnoth.offline.TaskHarness',case)
    def test_activity_hooks_are_scoped(self):
        source=(SOURCES/'OfflineGameActivity.java').read_text()
        self.assertIn('if (!focused && !mBrokenLibraries) WesnothKeyboard.focusLost();',source)
        self.assertIn('public void finishGameTask()',source)
        self.assertIn('WesnothGameTask.finish(this)',source)
        self.assertIn('public void resetKeyboardModifiers()',source)
        self.assertNotIn('toLowerCase',source.split('public void resetKeyboardModifiers()',1)[1].split('// These method',1)[0])
        helper=(SOURCES/'WesnothGameTask.java').read_text()
        for forbidden in ('System.exit','killProcess','clearApplicationUserData','delete(','getFilesDir','setenv'):
            self.assertNotIn(forbidden,helper)

if __name__=='__main__':unittest.main(verbosity=2)
