// SPDX-License-Identifier: GPL-2.0-or-later
namespace {
std::string gallery_window(){
    using namespace product_ui;const std::string id="alliance_hero_gallery";auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config def;auto& grid=product_ui::window(def,id);product_ui::text(grid,"gallery_title","My Heroes","alliance_title");
    auto& search_grid=product_ui::cell(product_ui::row(grid),"grid");auto& search=product_ui::row(search_grid);product_ui::cell(search,"text_box","hero_search")["hint_text"]="Find a hero";product_ui::cell(search,"menu_button","hero_filter")["definition"]="alliance_menu";product_ui::cell(search,"label","hero_page")["definition"]="alliance_caption";
    auto& body=product_ui::cell(product_ui::row(grid,1),"grid");auto& columns=product_ui::row(body,1);auto& cards=product_ui::cell(columns,"grid","",3);auto& detail=product_ui::cell(columns,"grid","",2);
    for(int y=0;y<2;++y){auto& r=product_ui::row(cards,1);for(int x=0;x<4;++x){const auto suffix=std::to_string(y*4+x);auto& card=product_ui::cell(r,"grid","",1,2);card["id"]="card_"+suffix;
        auto& art=product_ui::cell(product_ui::row(card),"image","card_art_"+suffix,1,2);art["definition"]="centered";
        auto& name=product_ui::cell(product_ui::row(card),"label","card_name_"+suffix,1,2);name["definition"]="alliance_caption";name["wrap"]=true;name["characters_per_line"]=14;
        auto& state=product_ui::cell(product_ui::row(card),"label","card_state_"+suffix,1,2);state["definition"]="alliance_caption";state["characters_per_line"]=14;
        product_ui::cell(product_ui::row(card),"progress_bar","card_progress_"+suffix,1,2);
        product_ui::cell(product_ui::row(card),"button","card_view_"+suffix,1,2)["label"]="View";
    }}
    product_ui::text(detail,"hero_name","","alliance_title");product_ui::cell(product_ui::row(detail),"image","hero_portrait")["definition"]="centered";
    product_ui::text(detail,"hero_availability","","alliance_caption");product_ui::cell(product_ui::row(detail),"progress_bar","hero_progress");product_ui::text(detail,"hero_progress_label","","alliance_caption");
    product_ui::cell(product_ui::row(detail),"menu_button","hero_form")["definition"]="alliance_menu";
    auto& lore=product_ui::cell(product_ui::row(detail,1),"scroll_label","hero_biography");lore["wrap"]=true;lore["use_markup"]=false;
    auto& footer_grid=product_ui::cell(product_ui::row(grid),"grid");auto& footer=product_ui::row(footer_grid);product_ui::cell(footer,"button","hero_previous")["label"]="Previous";
    product_ui::cell(footer,"button","hero_next")["label"]="Next";product_ui::cell(footer,"button","hero_use")["label"]="Use hero";product_ui::cell(footer,"button","cancel")["label"]="Back";
    windows.emplace(id,gui2::builder_window(def));return id;
}
class HeroGallery final:public gui2::dialogs::modal_dialog {
public:
    HeroGallery(const std::vector<HeroGalleryEntry>& entries,bool selection):modal_dialog(gallery_window()),entries_(entries),selection_(selection){set_allow_plugin_skip(false);}
    HeroGalleryResult result;
private:
    const std::vector<HeroGalleryEntry>& entries_;bool selection_;std::vector<int> visible_;std::size_t page_=0;int selected_=-1;
    const std::string& window_id()const override{static const std::string id="alliance_hero_gallery";return id;}
    void detail(bool reset_form=true){
        const auto* entry=selected_>=0?&entries_.at(selected_):nullptr;
        find_widget<gui2::label>("hero_name").set_label(entry?entry->name:"No heroes found");
        find_widget<gui2::image>("hero_portrait").set_label(entry?product_ui::artwork(entry->image)+"~SCALE_INTO(180,110)"+(entry->owned?"":"~GS()"):"misc/blank-hex.png");
        find_widget<gui2::label>("hero_availability").set_label(entry?entry->availability:"");
        auto& progress=find_widget<gui2::progress_bar>("hero_progress");progress.set_visible(entry&&entry->owned?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);progress.set_percentage(entry?std::max(0,entry->progress_percent):0);
        find_widget<gui2::label>("hero_progress_label").set_label(entry?entry->progress_label:"");
        auto& forms=find_widget<gui2::menu_button>("hero_form");
        if(reset_form){std::vector<config> values;if(entry)for(const auto& form:entry->forms){config c;c["label"]=form.title;values.push_back(std::move(c));}if(values.empty()){config c;c["label"]="No earned forms";values.push_back(c);}forms.set_values(values);}
        forms.set_active(entry&&entry->owned&&!entry->forms.empty());
        std::string text=entry?entry->biography:"";
        if(entry&&entry->owned&&!entry->forms.empty())text=entry->forms.at(forms.get_value()).detail+"\n\n"+text;
        find_widget<gui2::scroll_label>("hero_biography").set_label(text);
        find_widget<gui2::button>("hero_use").set_active(selection_&&entry&&entry->owned&&!entry->forms.empty());
    }
    void cards(){
        for(std::size_t n=0;n<8;++n){const auto i=page_*8+n;const bool present=i<visible_.size();const auto suffix=std::to_string(n);
            find_widget<gui2::grid>("card_"+suffix).set_visible(present?gui2::widget::visibility::visible:gui2::widget::visibility::hidden);if(!present)continue;
            const auto& hero=entries_[visible_[i]];find_widget<gui2::image>("card_art_"+suffix).set_label(product_ui::artwork(hero.image)+"~SCALE_INTO(120,80)"+(hero.owned?"":"~GS()"));
            find_widget<gui2::label>("card_name_"+suffix).set_label(hero.name);find_widget<gui2::label>("card_state_"+suffix).set_label(hero.owned?hero.card_progress:"Locked");
            auto& progress=find_widget<gui2::progress_bar>("card_progress_"+suffix);progress.set_percentage(std::max(0,hero.progress_percent));progress.set_visible(hero.owned?gui2::widget::visibility::visible:gui2::widget::visibility::hidden);
            find_widget<gui2::button>("card_view_"+suffix).set_label(visible_[i]==selected_?"Selected":"View");
        }
        find_widget<gui2::button>("hero_previous").set_active(page_>0);find_widget<gui2::button>("hero_next").set_active((page_+1)*8<visible_.size());
        find_widget<gui2::label>("hero_page").set_label(std::to_string(visible_.size())+" heroes · "+std::to_string(page_+1)+" / "+std::to_string(std::max<std::size_t>(1,(visible_.size()+7)/8)));
        invalidate_layout();
    }
    void filter(){
        const auto query=lower(find_widget<gui2::text_box>("hero_search").get_value());const auto mode=find_widget<gui2::menu_button>("hero_filter").get_value();visible_.clear();
        for(std::size_t i=0;i<entries_.size();++i){const auto& h=entries_[i];if((mode==1&&!h.owned)||(mode==2&&h.owned)||lower(h.name).find(query)==std::string::npos)continue;visible_.push_back(i);}
        page_=0;selected_=visible_.empty()?-1:visible_.front();cards();detail();
    }
    void pre_show()override {
        std::vector<config> choices;for(const auto* name:{"All heroes","Owned","Locked"}){config c;c["label"]=name;choices.push_back(c);}find_widget<gui2::menu_button>("hero_filter").set_values(choices,selection_?1:0);
        find_widget<gui2::label>("gallery_title").set_label(selection_?"Choose hero":"My Heroes");
        find_widget<gui2::button>("hero_use").set_visible(selection_?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);
        gui2::event::connect_signal_notify_modified(find_widget<gui2::text_box>("hero_search"),[this](auto&&...){filter();});
        gui2::event::connect_signal_notify_modified(find_widget<gui2::menu_button>("hero_filter"),[this](auto&&...){filter();});
        gui2::event::connect_signal_notify_modified(find_widget<gui2::menu_button>("hero_form"),[this](auto&&...){detail(false);});
        for(std::size_t n=0;n<8;++n)gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("card_view_"+std::to_string(n)),[this,n](auto&&...){const auto i=page_*8+n;if(i<visible_.size()){selected_=visible_[i];cards();detail();}});
        gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("hero_previous"),[this](auto&&...){if(page_){--page_;cards();}});
        gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("hero_next"),[this](auto&&...){if((page_+1)*8<visible_.size()){++page_;cards();}});
        gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("hero_use"),[this](auto&&...){if(selected_>=0&&entries_[selected_].owned&&!entries_[selected_].forms.empty()){result={selected_,static_cast<int>(find_widget<gui2::menu_button>("hero_form").get_value())};set_retval(gui2::retval::OK);}});
        set_enter_disabled(true);filter();
    }
};
}
HeroGalleryResult show_hero_gallery(const std::vector<HeroGalleryEntry>& entries,bool select_owned){HeroGallery dialog(entries,select_owned);dialog.show();return dialog.result;}
