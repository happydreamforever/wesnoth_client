// SPDX-License-Identifier: GPL-2.0-or-later
// Pinned Wesnoth 1.19.26 data/lua/wml/harm_unit.lua with explicit XP credit at its four award sites.
static constexpr const char story_harm_unit_lua[] = R"STORYLUA(local utils = wesnoth.require "wml-utils"
local wml_actions = wesnoth.wml_actions
local T = wml.tag

function wml_actions.harm_unit(cfg)
	local filter = wml.get_child(cfg, "filter") or wml.error("[harm_unit] missing required [filter] tag")
	-- we need to use shallow_literal field, to avoid raising an error if $this_unit (not yet assigned) is used
	if not wml.shallow_literal(cfg).amount then wml.error("[harm_unit] has missing required amount= attribute") end
	local variable = cfg.variable -- kept out of the way to avoid problems
	local _ = wesnoth.textdomain "wesnoth"
	-- #textdomain wesnoth
	local harmer

	local function toboolean( value ) -- helper for animate fields
		-- units will be animated upon leveling or killing, even
		-- with special attacker and defender values
		if value then return true
		else return false end
	end

	local this_unit <close> = utils.scoped_var("this_unit")

	for index, unit_to_harm in ipairs(wesnoth.units.find_on_map(filter)) do
		if unit_to_harm.valid then
			-- block to support $this_unit
			wml.variables["this_unit"] = nil -- clearing this_unit
			wml.variables["this_unit"] = unit_to_harm.__cfg -- cfg field needed
			local amount = tonumber(cfg.amount)
			local animate = cfg.animate -- attacker and defender are special values
			local delay = cfg.delay or 500
			local kill = cfg.kill
			local fire_event = cfg.fire_event
			local primary_attack = wml.get_child(cfg, "primary_attack")
			local secondary_attack = wml.get_child(cfg, "secondary_attack")
			local harmer_filter = wml.get_child(cfg, "filter_second")
			local experience = cfg.experience
			local resistance_multiplier = tonumber(cfg.resistance_multiplier) or 1
			if harmer_filter then harmer = wesnoth.units.find_on_map(harmer_filter)[1] end
			-- end of block to support $this_unit

			if animate then
				if animate ~= "defender" and harmer and harmer.valid then
					wesnoth.interface.scroll_to_hex(harmer.x, harmer.y, true)
					wml_actions.animate_unit {
						flag = "attack",
						hits = true,
						with_bars = true,
						T.filter { id = harmer.id },
						T.primary_attack ( primary_attack ),
						T.secondary_attack ( secondary_attack ),
						T.facing { x = unit_to_harm.x, y = unit_to_harm.y },
					}
				end
				wesnoth.interface.scroll_to_hex(unit_to_harm.x, unit_to_harm.y, true)
			end

			-- the two functions below are taken straight from the C++ engine,
			-- utils.cpp and actions.cpp, with a few unuseful parts removed
			-- may be moved in helper.lua in 1.11
			local function round_damage( base_damage, bonus, divisor )
				local rounding
				if base_damage == 0 then return 0
				else
					if bonus < divisor or divisor == 1 then
						rounding = divisor / 2 - 0
					else
						rounding = divisor / 2 - 1
					end
					return math.max( 1, math.floor( ( base_damage * bonus + rounding ) / divisor ) )
				end
			end

			local function calculate_damage( base_damage, alignment, tod_bonus, resistance, modifier )
				local damage_multiplier = 100
				if alignment == "lawful" then
					damage_multiplier = damage_multiplier + tod_bonus
				elseif alignment == "chaotic" then
					damage_multiplier = damage_multiplier - tod_bonus
				elseif alignment == "liminal" then
					damage_multiplier = damage_multiplier + math.max(0, wesnoth.current.schedule.liminal_bonus - math.abs(tod_bonus))
				end
				local resistance_modified = resistance * modifier
				damage_multiplier = damage_multiplier * resistance_modified
				local damage = round_damage( base_damage, damage_multiplier, 10000 ) -- if harmer.status.slowed, this may be 20000 ?
				return damage
			end

			local damage = calculate_damage(
				amount,
				cfg.alignment or "neutral",
				wesnoth.schedule.get_illumination(unit_to_harm).lawful_bonus,
				100 - unit_to_harm:resistance_against( cfg.damage_type or "dummy" ),
				resistance_multiplier
			)

			if unit_to_harm.hitpoints <= damage then
				if kill == false then damage = unit_to_harm.hitpoints - 1
				else damage = unit_to_harm.hitpoints
				end
			end

			unit_to_harm.hitpoints = unit_to_harm.hitpoints - damage
			local text = string.format("%d%s", damage, "\n")
			local add_tab = false
			local gender = unit_to_harm.__cfg.gender

			local function set_status(name, male_string, female_string, sound)
				if not cfg[name] or unit_to_harm.status[name] then return end
				if gender == "female" then
					text = string.format("%s%s%s", text, tostring(female_string), "\n")
				else
					text = string.format("%s%s%s", text, tostring(male_string), "\n")
				end

				unit_to_harm.status[name] = true
				add_tab = true

				if animate and sound then -- for unhealable, that has no sound
					wesnoth.audio.play(sound)
				end
			end

			if not unit_to_harm.status.unpoisonable then
				set_status("poisoned", _"poisoned", _"female^poisoned", "poison.ogg")
			end
			set_status("slowed", _"slowed", _"female^slowed", "slowed.wav")
			set_status("petrified", _"petrified", _"female^petrified", "petrified.ogg")
			set_status("unhealable", _"unhealable", _"female^unhealable")

			-- Extract unit and put it back to update animation if status was changed
			unit_to_harm:extract()
			unit_to_harm:to_map(false)

			if add_tab then
				text = string.format("%s%s", "\t", text)
			end

			if animate and animate ~= "attacker" then
				if harmer and harmer.valid then
					wml_actions.animate_unit {
						flag = "defend",
						hits = true,
						with_bars = true,
						T.filter { id = unit_to_harm.id },
						T.primary_attack ( secondary_attack ),
						T.secondary_attack ( primary_attack ),
						T.facing { x = harmer.x, y = harmer.y },
					}
				else
					wml_actions.animate_unit {
						flag = "defend",
						hits = true,
						with_bars = true,
						T.filter { id = unit_to_harm.id },
						T.primary_attack ( secondary_attack ),
						T.secondary_attack ( primary_attack ),
					}
				end
			end

			wesnoth.interface.float_label( unit_to_harm.x, unit_to_harm.y, string.format( "<span foreground='red'>%s</span>", text ) )

			local xp_mode = {kill=false, attack=false, defend=false}
			local experience_split = tostring(experience):split()
			for _,opt in ipairs(experience_split) do
				if opt == 'true' or opt == 'yes' or opt == 'nil' then
					xp_mode.kill = true
					xp_mode.attack = true
					xp_mode.defend = true
				elseif opt == 'fight' then
					xp_mode.attack = true
					xp_mode.defend = true
				elseif opt == 'attack' then
					xp_mode.attack = true
				elseif opt == 'defend' then
					xp_mode.defend = true
				elseif opt == 'kill' then
					xp_mode.kill = true
				elseif not (opt == 'no' or opt == 'false') then
					wml.error('Invalid [harm_unit] experience: should be boolean or a list of one or more of the following: kill, fight, attack, defend')
				end
				-- with 'no' or 'false' preserve previous value
			end
			if #experience_split == 0 then
				xp_mode = {kill=true, attack=true, defend=true}
			end

			local function calc_xp( level ) -- to calculate the experience in case of kill
				if level == 0 then return math.ceil(wesnoth.game_config.kill_experience / 2)
				else return level * wesnoth.game_config.kill_experience end
			end

			if harmer and harmer.valid
				and wesnoth.sides.is_enemy( unit_to_harm.side, harmer.side )
			then
				if unit_to_harm.hitpoints <= 0 then
					if xp_mode.kill then
						harmer.unified_story_credit = calc_xp( unit_to_harm.level )
harmer.experience = harmer.experience + calc_xp( unit_to_harm.level )
					elseif xp_mode.attack then
						harmer.unified_story_credit = wesnoth.game_config.combat_experience * unit_to_harm.level
harmer.experience = harmer.experience + wesnoth.game_config.combat_experience * unit_to_harm.level
					end
				else
					if xp_mode.defend then
						unit_to_harm.unified_story_credit = wesnoth.game_config.combat_experience * harmer.level
unit_to_harm.experience = unit_to_harm.experience + wesnoth.game_config.combat_experience * harmer.level
					end
					if xp_mode.attack then
						harmer.unified_story_credit = wesnoth.game_config.combat_experience * unit_to_harm.level
harmer.experience = harmer.experience + wesnoth.game_config.combat_experience * unit_to_harm.level
					end
				end
			end

			local unit_to_harm_id = unit_to_harm.id
			if kill ~= false and unit_to_harm.hitpoints <= 0 then
				wml_actions.kill { id = unit_to_harm.id, animate = toboolean( animate ), fire_event = fire_event, harmer and T.secondary_unit { id = harmer.id } }
			end

			if animate then
				wesnoth.interface.delay(delay)
			end

			if variable then
				wml.variables[string.format("%s[%d]", variable, index - 1)] = { id = unit_to_harm_id, harm_amount = damage }
			end

			-- both units may no longer be alive at this point, so double check
			if xp_mode.defend and unit_to_harm and unit_to_harm.valid then
				unit_to_harm:advance(toboolean(animate), fire_event ~= false)
			end

			if (xp_mode.attack or xp_mode.kill) and harmer and harmer.valid then
				harmer:advance(toboolean(animate), fire_event ~= false)
			end
		end

		wml_actions.redraw {}
	end
end
)STORYLUA";

// Pinned modify_unit.lua: explicit additive experience formulas only.
static constexpr const char story_modify_unit_lua[] = R"STORYLUA(local utils = wesnoth.require "wml-utils"
local wml_actions = wesnoth.wml_actions

-- The [modify_unit] implementation.
-- Implementation detail: every affected unit is passed through [unstore_unit], so gets
-- that tag's implementation's handling of special cases such as x,y=recall,recall.

function wml_actions.modify_unit(cfg)
	local unit_variable = "LUA_modify_unit"

	local replace_mode = cfg.mode == "replace"

	local function handle_attributes(child_cfg, unit_path, toplevel)
		for current_key, current_value in pairs(wml.shallow_parsed(child_cfg)) do
			if type(current_value) ~= "table" and (not toplevel or (current_key ~= "type" and current_key ~= "mode")) then
				wml.variables[string.format("%s.%s", unit_path, current_key)] = current_value
			end
		end
	end

	local function handle_child(child_cfg, unit_path)
		local children_handled = {}
		child_cfg = wml.shallow_parsed(child_cfg)
		handle_attributes(child_cfg, unit_path)

		for current_index, current_table in ipairs(child_cfg) do
			local current_tag = current_table[1]
			local tag_index = children_handled[current_tag] or 0
			handle_child(current_table[2], string.format("%s.%s[%u]",
				unit_path, current_tag, tag_index))
			children_handled[current_tag] = tag_index + 1
		end
	end

	local filter = wml.get_child(cfg, "filter") or wml.error "[modify_unit] missing required [filter] tag"
	local function handle_unit(unit_num)
		local children_handled = {}
		local unit_path = string.format("%s[%u]", unit_variable, unit_num)
		local this_unit = wml.variables[unit_path]
		wml.variables["this_unit"] = this_unit
		handle_attributes(cfg, unit_path, true)

		for current_index, current_table in ipairs(wml.shallow_parsed(cfg)) do
			local current_tag = current_table[1]
			if current_tag == "filter" then
				goto skip
			elseif current_tag == "object" or current_tag == "trait" or current_tag == "advancement" then
				local mod = current_table[2]
				if mod.delayed_variable_substitution then
					mod = wml.literal(mod)
				else
					mod = wml.parsed(mod)
				end
				local unit = wml.variables[unit_path]
				unit = wesnoth.units.create(unit)
				unit:add_modification(current_tag, mod)
				unit = unit.__cfg;
				wml.variables[unit_path] = unit
			elseif current_tag == "effect" then
				local mod = current_table[2]
				local apply_to = mod.apply_to
				if wesnoth.effects[apply_to] then
					local unit = wml.variables[unit_path]
					unit = wesnoth.units.create(unit)
					wesnoth.effects[apply_to](unit, mod)
					unit = unit.__cfg;
					wml.variables[unit_path] = unit
				else
					wml.error("[modify_unit] had invalid [effect]apply_to value")
				end
			elseif current_tag == "set_variable" then
				local unit = wesnoth.units.create(wml.variables[unit_path])
				wesnoth.wml_actions.set_variable(current_table[2], unit.variables)
				wml.variables[unit_path] = unit.__cfg
			elseif current_tag == "set_variables" then
				local unit = wesnoth.units.create(wml.variables[unit_path])
				wesnoth.wml_actions.set_variables(current_table[2], unit.variables)
				wml.variables[unit_path] = unit.__cfg
			elseif current_tag == "clear_variable" then
				local unit = wesnoth.units.create(wml.variables[unit_path])
				wesnoth.wml_actions.clear_variable(current_table[2], unit.variables)
				wml.variables[unit_path] = unit.__cfg
			else
				if replace_mode then
					wml.variables[string.format("%s.%s", unit_path, current_tag)] = {}
				end
				local tag_index = children_handled[current_tag] or 0
				handle_child(current_table[2], string.format("%s.%s[%u]",
					unit_path, current_tag, tag_index))
				children_handled[current_tag] = tag_index + 1
			end
			::skip::
		end

		if cfg.type then
			if cfg.type ~= "" then wml.variables[unit_path .. ".advances_to"] = cfg.type end
			wml.variables[unit_path .. ".experience"] = wml.variables[unit_path .. ".max_experience"]
		end
		-- Credit only an explicit additive XP award. Absolute restoration,
		-- type transformation and maximum-XP normalization are excluded.
		local raw_xp = wml.shallow_literal(cfg).experience
		if not cfg.type and type(raw_xp)=="string" and
		   (raw_xp:match("%$this_unit%.experience%s*%+") or raw_xp:match("%$unit%.experience%s*%+")) then
			local updated = wesnoth.units.create(wml.variables[unit_path])
			local earned = updated.experience - this_unit.experience
			if earned > 0 then updated.unified_story_credit = earned end
			wml.variables[unit_path] = updated.__cfg
		end
		wml_actions.kill({ id = this_unit.id, animate = false })
		wml_actions.unstore_unit { variable = unit_path }
	end

	wml_actions.store_unit { wml.tag.filter(filter), variable = unit_variable }
	local max_index = wml.variables[unit_variable .. ".length"] - 1

	local this_unit <close> = utils.scoped_var("this_unit")
	for current_unit = 0, max_index do
		handle_unit(current_unit)
	end

	wml.variables[unit_variable] = nil
end
)STORYLUA";

// Exact reviewed stored-unit rewards in Liberty's raid and dead-fish events.
static constexpr const char story_stored_bonus_lua[] = R"STOREDXP(
-- Preload may run again when a prepared scenario or save is loaded.
-- Keep the underlying implementation rather than chaining our own wrapper.
local original_set_variable = wesnoth.wml_actions.__unified_story_base_set_variable or wesnoth.wml_actions.set_variable
wesnoth.wml_actions.__unified_story_base_set_variable = original_set_variable
wesnoth.wml_actions.set_variable = function(cfg, variables)
 local campaign = wml.variables["unified_story.campaign_id"]
 local scenario = wml.variables["unified_story.scenario_id"]
 local amount = tonumber(cfg.add)
 local award = variables == nil and campaign == "Liberty" and cfg.name == "stored_unit.experience" and
   ((scenario == "01_The_Raid" and amount == 2) or
    (scenario == "03_A_Strategy_of_Hope" and amount == 6))
 original_set_variable(cfg, variables)
 if award then
  local stored = wml.variables["stored_unit"]
  local u = wesnoth.units.get(stored.id)
  if not u or u.x ~= tonumber(stored.x) or u.y ~= tonumber(stored.y) or u.side ~= tonumber(stored.side) then
   wml.error("The reviewed story XP award no longer matches its map unit")
  end
  u.unified_story_credit = amount
  wml.variables["stored_unit.variables"] = wml.get_child(u.__cfg, "variables") or {}
 end
end
)STOREDXP";
