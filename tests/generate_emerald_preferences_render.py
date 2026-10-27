"""Render every actual Preferences tab at top/end with native scope/geometry checks.

Uses the Emerald product fixture only for engine setup. No account, database or
saved game is modified. The display-mode shim is fixture-only for SDL dummy.
"""
from pathlib import Path
import argparse
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
options = parser.parse_args()
root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / 'tests/generate_emerald_ui_render.py'),
                '--output', str(options.output)], check=True)
p = options.output / 'render_probe.cpp'
source = p.read_text()
start = source.index('class Monitor:public events::pump_monitor')
end = source.index('class CitadelHome:', start)
source = source[:start] + r'''
unsigned prefs_tab=0,prefs_subtab=0;bool prefs_bottom=false;rect footer_before;
class Monitor:public events::pump_monitor {void process()override{
 if(!watched)return;++ticks;
 if(phase.compare(0,5,"auth-")==0) {
  if(ticks<12)return;auto* window=watched;verify_emerald_auth_geometry(*window);
  auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Authentication screenshot failed");
  const auto box=window->get_rectangle();geometry<<phase<<" panel "<<box.x<<' '<<box.y<<' '<<box.w<<' '<<box.h<<'\n';geometry.flush();
  watched=nullptr;auto& action=window->find_widget<gui2::button>(click);action.fire(gui2::event::LEFT_BUTTON_CLICK,action);return;
 }
 if(ticks==2&&!prefs_bottom){auto& pager=watched->find_widget<gui2::tab_container>("pager");if(pager.get_tab_count()!=6)throw std::runtime_error("Expected six preference tabs");pager.select_tab(prefs_tab);if(prefs_tab==4)watched->find_widget<gui2::tab_container>("multiplayer_pager").select_tab(prefs_subtab);}
 if(ticks<12)return;auto* window=watched;
 verify_native_preferences_geometry(*window,!prefs_bottom);
 auto& scroll=window->find_widget<gui2::scrollbar_panel>("alliance_preferences_scroll");
 const auto footer=window->find_widget<gui2::button>("ok").get_rectangle();
 const auto name=phase+(prefs_bottom?"-bottom":"-top");
 geometry<<name<<" Close "<<footer.x<<' '<<footer.y<<' '<<footer.w<<' '<<footer.h<<'\n';
 const auto pages=scroll.get_rectangle();geometry<<name<<" Pages "<<pages.x<<' '<<pages.y<<' '<<pages.w<<' '<<pages.h<<'\n';
 auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+name+".bmp").c_str()))throw std::runtime_error("Preferences screenshot failed");geometry.flush();
 if(!prefs_bottom){footer_before=footer;scroll.scroll_vertical_scrollbar(gui2::scrollbar_base::END);if(prefs_tab==1||prefs_tab==5)window->find_widget<gui2::listbox>(prefs_tab==1?"list_hotkeys":"advanced_prefs").scroll_vertical_scrollbar(gui2::scrollbar_base::END);prefs_bottom=true;ticks=0;return;}
 if(!scroll.vertical_scrollbar_at_end())throw std::runtime_error("Preferences did not scroll to the bottom");
 if((prefs_tab==1||prefs_tab==5)&&!window->find_widget<gui2::listbox>(prefs_tab==1?"list_hotkeys":"advanced_prefs").vertical_scrollbar_at_end())throw std::runtime_error("Preferences inner list did not scroll to end");
 if(prefs_tab==1||prefs_tab==5) {
  auto& list=window->find_widget<gui2::listbox>(prefs_tab==1?"list_hotkeys":"advanced_prefs");
  const auto last=list.get_row_grid(list.get_item_count()-1)->get_rectangle();
  if(last.y<pages.y || last.y+last.h>pages.y+pages.h)throw std::runtime_error("Final inner-list row is not visible after scrolling");
  geometry<<name<<" LastRow "<<last.x<<' '<<last.y<<' '<<last.w<<' '<<last.h<<'\n';geometry.flush();
 }
 if(footer!=footer_before)throw std::runtime_error("Scrolling moved Preferences Close");
 watched=nullptr;window->find_widget<gui2::button>("ok").fire(gui2::event::LEFT_BUTTON_CLICK,window->find_widget<gui2::button>("ok"));
}};

''' + source[end:]
source = source[:source.index('bool auth_render_fixture(game_launcher&)')] + r'''
bool auth_render_fixture(game_launcher&) {
 using namespace alliance_game;Monitor monitor;game_config_manager::get()->load_game_config_for_create(true);
 verify_native_menu_theme_boundaries();
 for(const std::string theme:{"default","modern"}) {
  gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();
  {AuthenticationBackdrop backdrop;std::string username,password;
   phase="auth-login-"+theme;click="cancel";{CredentialsDialog login(username,password,"",false,true);login.show();if(login.get_retval()!=13)throw std::runtime_error("Login X lost Quit action");}
   phase="auth-signup-"+theme;click="signup";{CredentialsDialog signup(username,password,"",true,true);signup.show();if(signup.get_retval()!=11)throw std::runtime_error("Signup Back lost replacement action");}
   phase="auth-recovery-"+theme;click="cancel";{CredentialsDialog recovery(username,password,"Your session expired. Sign in to the same account to continue.",false,false);recovery.show();if(recovery.get_retval()!=13)throw std::runtime_error("Recovery X lost Quit action");}
  }
  for(prefs_tab=0;prefs_tab<6;++prefs_tab)for(prefs_subtab=0;prefs_subtab<(prefs_tab==4?2u:1u);++prefs_subtab){prefs_bottom=false;phase="native-preferences-"+theme+"-"+std::to_string(prefs_tab)+(prefs_tab==4?"-"+std::to_string(prefs_subtab):"");gui2::dialogs::preferences_dialog preferences;if(menu_theme_depth()!=0)throw std::runtime_error("Preferences constructor scope leaked");preferences.show();if(menu_theme_depth()!=0)throw std::runtime_error("Preferences theme leaked");}
 }
 std::cout<<"EMERALD AUTH AND PREFERENCES RENDER PASS\n";return true;
}
void citadel_native_arm(gui2::window& window){if(window.id()=="preferences_dialog")arm(window);}
'''
p.write_text(source)
