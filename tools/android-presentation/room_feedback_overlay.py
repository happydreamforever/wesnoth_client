"""Guarded Android Room validation feedback; shared worker inputs stay intact."""
from hashlib import sha256


CREATION_PIN = 'c4fbefd86bd5df1c0826e5c383f3667b1557fe52521d41371357347cbbd3a23d'
ROOMS_PIN = 'd914da91b87df1bc2f79f5a2cd06b3821e204f96dd5bdc1dc43aa2c193d14cef'


def replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Room feedback presentation boundary differs; originals preserved.')
    return source.replace(before, after)


def generate_creation(source):
    if sha256(source).hexdigest() != CREATION_PIN:
        raise ValueError('Pinned Room creation source changed; originals preserved.')
    result = source.decode('utf-8')
    result = replace(result,
        '            try{auto number=[this](const char* id,int minimum,int maximum){const auto text=',
        '            std::string problem="Check the room settings.";\n'
        '            try{auto number=[this,&problem](const char* id,int minimum,int maximum){'
        'problem=std::string(id)=="gold"?"Gold must be 0–1000.":std::string(id)=="income"?'
        '"Base income must be 0–20.":"Turns must be 1–1000 or Unlimited.";const auto text=')
    result = replace(result,
        '                name=find_widget<gui2::text_box>("room_name").get_value();',
        '                problem="Enter a room name.";name=find_widget<gui2::text_box>("room_name").get_value();')
    result = replace(result,
        '                selected=find_widget<gui2::listbox>("maps").get_selected_row();',
        '                problem="Choose a battle map.";selected=find_widget<gui2::listbox>("maps").get_selected_row();')
    result = replace(result,
        '            }catch(...){find_widget<gui2::label>("room_error").set_label("Enter a name · Gold 0–1000 · Base income 0–20 · Turns 1–1000 or Unlimited");}',
        '            }catch(...){find_widget<gui2::label>("room_error").set_label(problem);invalidate_layout();'
        'gui2::show_message("Room settings",problem,gui2::dialogs::message::ok_button,false,false);}')
    return result.encode('utf-8')


def generate_rooms(source):
    if sha256(source).hexdigest() != ROOMS_PIN:
        raise ValueError('Pinned Room source changed; originals preserved.')
    return replace(source, b'#include "alliance_game/individual_creation.inc"',
                   b'#include "room_feedback_creation.inc"')
