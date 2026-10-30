// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/product_ui_layout.hpp"
#include "presentation_icons.hpp"
#include "alliance_game/emerald_chrome.hpp"
#include "gui/core/timer.hpp"
#include "gui/dialogs/tooltip.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/window.hpp"
#include <array>
#include <chrono>
#include <memory>

namespace alliance_game::mobile_home {
// Android presentation is isolated from the accepted native worker sources.
// Nine finite scale buckets avoid accumulating widget definitions while a
// desktop window is dragged through hundreds of intermediate dimensions.
inline double scale(){const auto size=video::game_canvas_size();return std::floor(std::clamp(std::min(size.x/1280.,size.y/720.),1.,2.)*8.)/8.;}
inline int px(int value){return static_cast<int>(std::lround(value*scale()));}
inline std::string style(const std::string& name){return name+"_"+std::to_string(px(1000));}
struct Action {const char* id;const char* name;presentation_icons::Symbol symbol;int width,height,icon;};
inline constexpr std::array<Action,10> actions{{
    {"quit","Quit",presentation_icons::Symbol::quit,52,52,26},
    {"help","Help",presentation_icons::Symbol::help,52,52,26},
    {"preferences","Preferences",presentation_icons::Symbol::preferences,52,52,26},
    {"addons","My Heroes",presentation_icons::Symbol::heroes,52,52,26},
    {"achievements","Achievements",presentation_icons::Symbol::achievements,52,52,26},
    {"load","Load",presentation_icons::Symbol::load,148,172,60},
    {"multiplayer","Room",presentation_icons::Symbol::room,164,190,76},
    {"campaign","Campaign",presentation_icons::Symbol::campaign,184,224,96},
    {"community","Team",presentation_icons::Symbol::team,164,190,76},
    {"editor","Map Editor",presentation_icons::Symbol::editor,148,172,60}
}};

inline config& plain_cell(config& row,const char* type,const std::string& id="",int grow=0) {
    auto& column=row.add_child("column");column["grow_factor"]=grow;column["horizontal_alignment"]="center";column["vertical_alignment"]="center";
    auto& child=column.add_child(type);if(*type&&std::string(type)!="grid"){child["id"]=id;child["definition"]="default";}return child;
}
inline void spacer(config& row,int width,int height,int grow=0){auto& s=plain_cell(row,"spacer","",grow);s["width"]=width;s["height"]=height;}
inline void label_style(const char* id,int width,int height,int font,const char* colour,bool script=false) {
    config definition;definition["id"]=style(id);definition["description"]="Home title and profile";auto& r=definition.add_child("resolution");width=px(width);height=px(height);font=px(font);
    r["min_width"]=width;r["default_width"]=width;r["max_width"]=width;r["min_height"]=height;r["default_height"]=height;r["max_height"]=height;r["text_font_size"]=font;

    for(const char* state:{"state_enabled","state_disabled"}){auto& d=r.add_child(state).add_child("draw");auto& t=d.add_child("text");
        t["x"]=script?"((width - text_width) / 2)":"0";t["y"]="((height - text_height) / 2)";t["w"]="(text_width)";t["h"]="(text_height)";
        t["maximum_width"]="(width)";t["maximum_height"]="(height)";t["font_size"]=font;
        t["color"]=colour;t["text"]="(text)";t["text_markup"]="(text_markup)";
        if(script){config shadow=t;shadow["x"]="((width - text_width) / 2 + "+std::to_string(px(2))+")";shadow["y"]="((height - text_height) / 2 + "+std::to_string(px(2))+")";shadow["color"]="10,23,17,220";d.clear_children("text");emerald_chrome::reset_markup_cache(d);d.add_child("text",shadow);emerald_chrome::reset_markup_cache(d);shadow["x"]="((width - text_width) / 2)";shadow["y"]="((height - text_height) / 2)";shadow["color"]=colour;d.add_child("text",shadow);}}
    gui2::add_single_widget_definition("label",style(id),definition);
}
inline void action_style(const Action& action,bool utility) {
    const std::string id=style("alliance_mobile_home_"+std::string(action.id));const int width=px(action.width),height=px(action.height),icon_size=px(action.icon);config definition;definition["id"]=id;definition["description"]=action.name;
    auto& r=definition.add_child("resolution");r["min_width"]=width;r["default_width"]=width;r["max_width"]=width;
    r["min_height"]=height;r["default_height"]=height;r["max_height"]=height;r["text_font_size"]=px(22);
    const bool campaign=std::string(action.id)=="campaign";
    const std::array<const char*,4> states{{"state_enabled","state_disabled","state_pressed","state_focused"}};
    for(std::size_t i=0;i<states.size();++i){auto& d=r.add_child(states[i]).add_child("draw");
        if(utility){
            auto& ring=d.add_child("circle");ring["x"]=width/2;ring["y"]=height/2;ring["radius"]=width/2-px(2);ring["border_thickness"]=px(2);
            ring["border_color"]=i==3?product_ui::ivory:"(if(home_focus, [246,233,201,255], [225,188,107,255]))";
            ring["fill_color"]=i==2?product_ui::teal:product_ui::stone;
            auto& inner=d.add_child("circle");inner["x"]=width/2;inner["y"]=height/2;inner["radius"]=width/2-px(5);inner["border_thickness"]=1;inner["border_color"]=product_theme::dark_gold_rgba;inner["fill_color"]="0,0,0,0";
        } else {
            auto& plaque=d.add_child("image");plaque["name"]=emerald_chrome::plaque(width,height,campaign,i==2||i==3,i==1);plaque["w"]=width;plaque["h"]=height;plaque["resize_mode"]="scale";
            auto& focus=d.add_child("image");focus["name"]="(if(home_focus, '"+emerald_chrome::plaque(width,height,campaign,true,i==1)+"', ''))";focus["w"]=width;focus["h"]=height;focus["resize_mode"]="scale";
        }
        auto& icon=d.add_child("image");icon["x"]=(width-icon_size)/2;icon["y"]=(utility?(height-icon_size)/2:px(campaign?39:31))+(i==2?px(2):0);icon["w"]=icon_size;icon["h"]=icon_size;icon["resize_mode"]="scale";
        icon["name"]=utility?presentation_icons::image(action.symbol,icon_size,i==1?0x89928Au:i==2||i==3?product_theme::ivory_rgb:product_theme::gold_rgb,std::clamp(video::get_pixel_scale(),2,4)):presentation_icons::emblem(action.symbol,icon_size,campaign);
        if(!utility){emerald_chrome::reset_markup_cache(d);auto& caption=d.add_child("text");caption["x"]="((width - text_width) / 2)";caption["y"]=height-px(campaign?75:65)+(i==2?px(2):0);caption["w"]="(text_width)";caption["h"]="(text_height)";caption["maximum_width"]=width-px(16);caption["font_size"]=px(campaign?27:23);caption["color"]=campaign?product_theme::ink_rgba:product_ui::ivory;caption["text"]=emerald_chrome::serif_text(action.name);caption["text_markup"]=true;
            emerald_chrome::ornament_canvas(d,px(campaign?40:32),"((width - "+std::to_string(px(campaign?40:32))+") / 2)",std::to_string(height-px(campaign?57:45)),campaign);}
    }
    gui2::add_single_widget_definition("button",id,definition);
}
inline void prepare_styles() {
    product_ui::prepare_theme();if(gui2::current_gui->second.widget_types["window"].count(style("alliance_mobile_home_scene")))return;
    for(std::size_t i=0;i<actions.size();++i)action_style(actions[i],i<5);
    label_style("alliance_mobile_home_kicker",760,36,30,product_ui::ivory,true);
    label_style("alliance_mobile_home_title",760,112,96,product_theme::light_gold_rgba,true);
    {config c;c["id"]=style("alliance_mobile_home_crest");c["description"]="Oak crest";auto& cr=c.add_child("resolution");cr["min_width"]=px(260);cr["default_width"]=px(260);cr["max_width"]=px(260);cr["min_height"]=px(30);cr["default_height"]=px(30);cr["max_height"]=px(30);
      auto& draw=cr.add_child("state_enabled").add_child("draw");emerald_chrome::ornament_canvas(draw,px(40),"((width - "+std::to_string(px(40))+") / 2)",std::to_string(-px(10)));
      for(int side=0;side<2;++side){auto& line=draw.add_child("line");line["x1"]=px(side?155:5);line["x2"]=px(side?255:105);line["y1"]=px(17);line["y2"]=px(17);line["color"]=product_ui::gold;}cr.add_child("state_disabled").add_child("draw");gui2::add_single_widget_definition("label",style("alliance_mobile_home_crest"),c);}
    label_style("alliance_mobile_home_name",172,26,19,product_ui::ivory);
    label_style("alliance_mobile_home_count",172,22,16,product_ui::gold);
    label_style("alliance_mobile_home_tip",240,32,20,product_ui::ivory);
    label_style("alliance_mobile_home_profile_tip",500,32,20,product_ui::ivory);
    config panel;panel["id"]=style("alliance_mobile_home_profile");panel["description"]="Signed-in player";auto& p=panel.add_child("resolution");
    const int horizontal_padding=px(200)-px(172),vertical_padding=px(60)-px(26)-px(22);
    p["left_border"]=horizontal_padding/2;p["right_border"]=horizontal_padding-horizontal_padding/2;
    p["top_border"]=vertical_padding/2;p["bottom_border"]=vertical_padding-vertical_padding/2;
    emerald_chrome::frame_canvas(p.add_child("background").add_child("draw"),emerald_chrome::Kind::profile,false,false,px(24));p.add_child("foreground").add_child("draw");
    gui2::add_single_widget_definition("panel",style("alliance_mobile_home_profile"),panel);
    config window;window["id"]=style("alliance_mobile_home_scene");window["description"]="Citadel at dawn";auto& r=window.add_child("resolution");
    auto& background=r.add_child("background").add_child("draw");product_ui::background_canvas(background);
    // Quiet the sky and foreground only behind the controls, preserving the
    // painted middle. Alpha layers are native canvas rectangles, not UI art.
    for(int band=0;band<96;++band){auto& top=background.add_child("rectangle");top["x"]=0;top["y"]=px(band*3);top["w"]="(width)";top["h"]=px((band+1)*3)-px(band*3);top["fill_color"]="7,27,21,"+std::to_string(70-band*70/95);
        auto& bottom=background.add_child("rectangle");bottom["x"]=0;bottom["y"]="(height - "+std::to_string(px((band+1)*3))+")";bottom["w"]="(width)";bottom["h"]=px((band+1)*3)-px(band*3);bottom["fill_color"]="7,27,21,"+std::to_string(105-band*105/95);}
    emerald_chrome::frame_canvas(r.add_child("foreground").add_child("draw"),emerald_chrome::Kind::panel,false,false,px(32),false);auto& column=product_ui::row(r.add_child("grid"),1).add_child("column");column["horizontal_grow"]=true;column["vertical_grow"]=true;column.add_child("grid")["id"]="_window_content_grid";
    gui2::add_single_widget_definition("window",style("alliance_mobile_home_scene"),window);
    config tip_frame;tip_frame["id"]=style("alliance_mobile_home_tip_frame");tip_frame["description"]="Action name";auto& frame=tip_frame.add_child("resolution");
    frame["left_border"]=px(10);frame["right_border"]=px(10);frame["top_border"]=px(10);frame["bottom_border"]=px(10);
    emerald_chrome::frame_canvas(frame.add_child("background").add_child("draw"),emerald_chrome::Kind::field,false,false,px(12));frame.add_child("foreground").add_child("draw");
    product_ui::cell(product_ui::row(frame.add_child("grid"),1),"grid","",1,0)["id"]="_window_content_grid";
    gui2::add_single_widget_definition("window",style("alliance_mobile_home_tip_frame"),tip_frame);
    config tip;tip["id"]=style("alliance_mobile_home_tooltip");tip["description"]="Action name";auto& tr=tip.add_child("resolution");tr["definition"]=style("alliance_mobile_home_tip_frame");tr["automatic_placement"]=false;
    tr["width"]=px(260);tr["height"]=px(52);
    tr["x"]="(max(8, min(screen_width - "+std::to_string(px(268))+", source_x + source_w / 2 - "+std::to_string(px(130))+")))";
    tr["y"]="(max(8, min(screen_height - "+std::to_string(px(60))+", if(source_y > screen_height / 2, source_y - "+std::to_string(px(62))+", source_y + source_h + "+std::to_string(px(10))+"))))";
    tr.add_child("tooltip")["id"]="tooltip";tr.add_child("helptip")["id"]="tooltip";
    auto& tip_label=plain_cell(product_ui::row(tr.add_child("grid")),"label","label");tip_label["definition"]=style("alliance_mobile_home_tip");tip_label["wrap"]=false;
    gui2::current_gui->second.window_types.emplace(style("alliance_mobile_home_tooltip"),gui2::builder_window(tip));
    tip["id"]=style("alliance_mobile_home_profile_tooltip");tr["width"]=px(520);
    tr["x"]="(max(8, min(screen_width - "+std::to_string(px(528))+", source_x + source_w / 2 - "+std::to_string(px(260))+")))";
    tip_label["definition"]=style("alliance_mobile_home_profile_tip");
    gui2::current_gui->second.window_types.emplace(style("alliance_mobile_home_profile_tooltip"),gui2::builder_window(tip));
}
inline std::string prepare_window() {
    prepare_styles();const std::string id=style("alliance_mobile_home");auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config definition;definition["id"]=id;definition["description"]="Wesnoth";auto& r=definition.add_child("resolution");r["definition"]=style("alliance_mobile_home_scene");r["automatic_placement"]=false;
    r["x"]=0;r["y"]=0;r["width"]="(screen_width)";r["height"]="(screen_height)";r.add_child("tooltip")["id"]=style("alliance_mobile_home_tooltip");r.add_child("helptip")["id"]=style("alliance_mobile_home_tooltip");
    auto& grid=r.add_child("grid");auto& header_column=product_ui::row(grid).add_child("column");header_column["horizontal_grow"]=true;header_column["border"]="top,left,right";header_column["border_size"]=px(24);
    auto& header=product_ui::row(header_column.add_child("grid"));spacer(header,1,px(60),1);
    for(std::size_t i=0;i<5;++i){auto& b=plain_cell(header,"button",actions[i].id);b["definition"]=style("alliance_mobile_home_"+std::string(actions[i].id));b["label"]=actions[i].name;b["tooltip"]=actions[i].name;b["return_value"]=0;if(i<4)spacer(header,px(8),1);}
    spacer(header,px(16),1);auto& profile=plain_cell(header,"panel","home_profile");profile["definition"]=style("alliance_mobile_home_profile");
    auto& profile_grid=profile.add_child("grid");auto& name=plain_cell(product_ui::row(profile_grid),"label","home_player");name["definition"]=style("alliance_mobile_home_name");name["wrap"]=false;name["label"]="Player";
    auto& count=plain_cell(product_ui::row(profile_grid),"label","home_heroes");count["definition"]=style("alliance_mobile_home_count");count["label"]="Heroes: 0";
    auto& kicker=plain_cell(product_ui::row(grid),"label","home_kicker");kicker["definition"]=style("alliance_mobile_home_kicker");kicker["label"]=emerald_chrome::serif_text(product_theme::title_kicker);kicker["use_markup"]=true;
    auto& crest=plain_cell(product_ui::row(grid),"label","home_crest");crest["definition"]=style("alliance_mobile_home_crest");
    auto& title=plain_cell(product_ui::row(grid),"label","home_title");title["definition"]=style("alliance_mobile_home_title");title["label"]=emerald_chrome::serif_text(product_theme::title_name);title["use_markup"]=true;
    spacer(product_ui::row(grid,1),1,1,1);
    auto& footer=product_ui::row(plain_cell(product_ui::row(grid),"grid"));spacer(footer,1,px(224),1);
    for(std::size_t i=5;i<actions.size();++i){if(i>5)spacer(footer,px(12),1);auto& column=footer.add_child("column");column["vertical_alignment"]="bottom";column["horizontal_alignment"]="center";auto& b=column.add_child("button");b["id"]=actions[i].id;b["definition"]=style("alliance_mobile_home_"+std::string(actions[i].id));b["label"]=actions[i].name;b["tooltip"]=actions[i].name;}
    spacer(footer,1,px(224),1);spacer(product_ui::row(grid),1,px(24));windows.emplace(id,gui2::builder_window(definition));return id;
}

inline void set_profile(gui2::window& window,const std::string& username,std::size_t heroes) {
    auto& name=window.find_widget<gui2::label>("home_player");name.set_label(username);name.set_tooltip(username);name.set_text_ellipse_mode(PANGO_ELLIPSIZE_END);
    window.find_widget<gui2::label>("home_heroes").set_label("Heroes: "+std::to_string(heroes));
}

// A press describes an action at 550ms. Its eventual release must never
// execute that action. The next distinct press starts a fresh gesture.
struct Press {
    bool down=false,described=false,cancelled=false;
    void begin(){down=true;described=cancelled=false;}
    bool describe(){if(!down||cancelled)return false;described=true;return true;}
    void cancel(){down=false;cancelled=true;}
    void release(){down=false;}
    bool accept_click(){const bool allow=!described&&!cancelled;down=false;return allow;}
};
class Interaction : public std::enable_shared_from_this<Interaction> {
    gui2::window& window_;std::array<Press,actions.size()> presses_{};std::array<std::size_t,actions.size()+1> timers_{};
    void stop(std::size_t i){if(timers_[i])gui2::remove_timer(timers_[i]);timers_[i]=0;}
    void describe(std::size_t i){auto& b=window_.find_widget<gui2::button>(actions[i].id);const auto box=b.get_rectangle();if(box.w&&box.h)gui2::dialogs::tip::show(style("alliance_mobile_home_tooltip"),actions[i].name,point{box.x+box.w/2,box.y},box);}
    void describe_profile(){auto& label=window_.find_widget<gui2::label>("home_player");const auto box=window_.find_widget<gui2::widget>("home_profile").get_rectangle();if(box.w&&box.h)gui2::dialogs::tip::show(style("alliance_mobile_home_profile_tooltip"),label.get_label(),point{box.x,box.y},box);}
    void focus(std::size_t i,bool focused){auto& b=window_.find_widget<gui2::button>(actions[i].id);for(int n=0;n<4;++n)b.get_canvas(n).set_variable("home_focus",wfl::variant(focused));b.queue_redraw();if(focused&&i<5)describe(i);else gui2::dialogs::tip::remove();}
public:
    explicit Interaction(gui2::window& window):window_(window){}
    ~Interaction(){for(std::size_t i=0;i<timers_.size();++i)stop(i);}
    void bind(){
        const auto self=shared_from_this();
        for(std::size_t i=0;i<actions.size();++i){auto& button=window_.find_widget<gui2::button>(actions[i].id);button.set_tooltip(actions[i].name);button.set_help_message(actions[i].name);
            for(int n=0;n<4;++n)button.get_canvas(n).set_variable("home_focus",wfl::variant(false));window_.add_to_tab_order(&button);
            button.connect_signal<gui2::event::LEFT_BUTTON_DOWN>([self,i](auto&&...){self->stop(i);self->presses_[i].begin();std::weak_ptr<Interaction> weak=self;
                self->timers_[i]=gui2::add_timer(std::chrono::milliseconds(550),[weak,i](std::size_t){if(auto owner=weak.lock()){owner->timers_[i]=0;if(owner->presses_[i].describe())owner->describe(i);}});},gui2::event::dispatcher::front_child);
            button.connect_signal<gui2::event::LEFT_BUTTON_UP>([self,i](auto&&...){self->stop(i);self->presses_[i].release();},gui2::event::dispatcher::front_child);
            button.connect_signal<gui2::event::MOUSE_LEAVE>([self,i](auto&&...){self->stop(i);if(self->presses_[i].down)self->presses_[i].cancel();},gui2::event::dispatcher::front_child);
            button.connect_signal<gui2::event::LEFT_BUTTON_CLICK>([self,i](gui2::widget&,auto,bool& handled,bool& halt){self->stop(i);if(!self->window_.find_widget<gui2::button>(actions[i].id).get_active()||!self->presses_[i].accept_click())handled=halt=true;else gui2::dialogs::tip::remove();},gui2::event::dispatcher::front_child);
            button.connect_signal<gui2::event::RECEIVE_KEYBOARD_FOCUS>([self,i](auto&&...){self->focus(i,true);});
            button.connect_signal<gui2::event::LOSE_KEYBOARD_FOCUS>([self,i](auto&&...){self->focus(i,false);});
            button.connect_signal<gui2::event::SDL_KEY_DOWN>([self,i](gui2::widget&,auto,bool& handled,bool& halt,SDL_Keycode key,SDL_Keymod mod,const std::string&){
                if(mod&(SDL_KMOD_CTRL|SDL_KMOD_ALT|SDL_KMOD_GUI))return;
                if(!self->window_.find_widget<gui2::button>(actions[i].id).get_active())return;
                if(key==SDLK_RETURN||key==SDLK_KP_ENTER||key==SDLK_SPACE){self->presses_[i].begin();self->presses_[i].release();auto& b=self->window_.find_widget<gui2::button>(actions[i].id);handled=halt=true;b.fire(gui2::event::LEFT_BUTTON_CLICK,b);}
                else if(key==SDLK_LEFT||key==SDLK_RIGHT||key==SDLK_UP||key==SDLK_DOWN){std::size_t next=i;const std::size_t begin=i<5?0:5,end=i<5?5:actions.size();
                    if(key==SDLK_LEFT)next=i==begin?end-1:i-1;else if(key==SDLK_RIGHT)next=i+1==end?begin:i+1;else next=i<5?i+5:i-5;
                    handled=halt=true;self->window_.keyboard_capture(&self->window_.find_widget<gui2::button>(actions[next].id));}
            },gui2::event::dispatcher::front_child);
        }
        auto& profile=window_.find_widget<gui2::label>("home_player");
        profile.connect_signal<gui2::event::LEFT_BUTTON_DOWN>([self](auto&&...){self->stop(actions.size());std::weak_ptr<Interaction> weak=self;
            self->timers_[actions.size()]=gui2::add_timer(std::chrono::milliseconds(550),[weak](std::size_t){if(auto owner=weak.lock()){owner->timers_[actions.size()]=0;owner->describe_profile();}});});
        profile.connect_signal<gui2::event::LEFT_BUTTON_UP>([self](auto&&...){self->stop(actions.size());});
        profile.connect_signal<gui2::event::MOUSE_LEAVE>([self](auto&&...){self->stop(actions.size());});
        profile.connect_signal<gui2::event::SHOW_TOOLTIP>([self](gui2::widget&,auto,bool& handled,bool& halt,const point&){self->describe_profile();handled=halt=true;},gui2::event::dispatcher::front_child);
        window_.connect_signal<gui2::event::SDL_ACTIVATE>([self](auto&&...){self->stop(actions.size());for(std::size_t i=0;i<actions.size();++i){self->stop(i);if(self->presses_[i].down)self->presses_[i].cancel();}});
        window_.keyboard_capture(&window_.find_widget<gui2::button>("campaign"));
    }
};
inline void bind(gui2::window& window){std::make_shared<Interaction>(window)->bind();}
}
