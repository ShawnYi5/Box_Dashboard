$('#use_clw_boot').click(function () {
	if ($(this).is(':checked')) {
        $('.disk_table_container[data-clw-disk=1]').show();
        //$('.disk_table_container[data-clw-disk=1] .header_check').prop('checked',true);
    } else {
        $('.disk_table_container[data-clw-disk=1]').hide();
        //$('.disk_table_container[data-clw-disk=1] .header_check').prop('checked',false);
    }
});


var disk_vol_info = null;

function GetHardDisks(jsonstr) {
	    disk_vol_info = null;
    $('.mywaite').hide();
    $('#use_clw_boot').prop('checked', false);
	{% if no_fast_boot %}
    $('#restore_model').prop('checked', false);
	{% else %}
	$('#restore_model').prop('checked', true);
	{% endif %}
    if (jsonstr.r != 0) {
        openErrorDialog({title: '错误', html: jsonstr.e});
        return;
    }

    $('#waring_type_describe').text(jsonstr.disk_type.type_describe);
	
	if(jsonstr.replace_efi)
	{
		$('#replace_efi_div').show();
	}
	else
	{
		$('#replace_efi_div').hide();
	}

    // 目标磁盘 升序排列
    jsonstr.destlist.sort(function (a, b) {
        if (a['bootable']) {
            return -1;
        }
        if (b['bootable']) {
            return 1;
        }
        return a['bytes'] - b['bytes'];
    });

    // 科力锐启动盘排最前，其次启动盘，再次其按大小升序排列
    jsonstr.srclist.sort(function (a, b) {
        if (a.clw_disk) {
            return -1;
        }
        if (b.clw_disk) {
            return 1;
        }
        if (a['bootable']) {
            return -1;
        }
        if (b['bootable']) {
            return 1;
        }
        return a['bytes'] - b['bytes'];
    });

    var time_str = get_restore_time();

    $('#current_point_time').text(time_str.replace('T', ' '));
    disk_vol_info = {};// 存放磁盘及其卷信息
    var selected_index = [];
    $.each(jsonstr.srclist, function (i, item) {
        var target_div = $($('#div_content').html());
        target_div.find('select').empty();
        target_div.find('ul').empty();
        //添加源和目标的磁盘信息
        if (item.bootable) {
            if (item.clw_disk){
                var prefix = '(加载盘)';
                target_div.find('.header_check').prop('checked', false).attr('value', item.id + '|' + item.bytes).addClass('isbootable').addClass('clw_disk');
            }
            else {
                var prefix = '(引导盘)';
                target_div.find('.header_check').prop('checked', true).attr('onclick', 'return false').attr('value', item.id + '|' + item.bytes).addClass('isbootable');
            }
            var srcdiskname = prefix + '(' + (item.bytes / Math.pow(1024, 3)).toFixed(2) + 'GB)' + item.name;
            target_div.find('.disk_name').text(srcdiskname);
            for (var i = 0; i < jsonstr.destlist.length; i++) {
                if (jsonstr.destlist[i].bootable) {

					var tmphtml = '<option value="{disk_id}|{disk_size}" class="isbootable">{disk_name}</option>';
					selected_index.push(i);

                }
                else {
                    var tmphtml = "<option value='{disk_id}|{disk_size}'>{disk_name}</option>";
                }
                tmphtml = tmphtml.replace('{disk_size}', jsonstr.destlist[i].bytes);
                tmphtml = tmphtml.replace('{disk_id}', jsonstr.destlist[i].id);
                tmphtml = tmphtml.replace('{disk_name}', jsonstr.destlist[i].name);
                tmphtml = set_html_elem_title_prop(tmphtml, jsonstr.destlist[i].disk_sn);
                target_div.find('select').append(tmphtml);

            }
        }
        else {
            var normal_disk_name = '(' + (item.bytes / Math.pow(1024, 3)).toFixed(2) + 'GB)' + item.name;
            target_div.find('.header_check').attr('value', item.id + '|' + item.bytes);
            target_div.find('.disk_name').text(normal_disk_name);
            var has_find = false;
            for (var i = 0; i < jsonstr.destlist.length; i++) {
                var is_big = jsonstr.destlist[i].bytes >= item.bytes;
                var not_bootable = !jsonstr.destlist[i].bootable;
                var not_use = selected_index.indexOf(i) == -1;
                if (not_bootable && not_use && is_big && !has_find) {
                    var tmphtml = '<option value="{disk_id}|{disk_size}" selected="selected" >{disk_name}</option>';
                    selected_index.push(i);
                    has_find = true;
                } else {
                    var tmphtml = "<option value='{disk_id}|{disk_size}'>{disk_name}</option>";
                }
                tmphtml = tmphtml.replace('{disk_size}', jsonstr.destlist[i].bytes);
                tmphtml = tmphtml.replace('{disk_id}', jsonstr.destlist[i].id);
                tmphtml = tmphtml.replace('{disk_name}', jsonstr.destlist[i].name);
                tmphtml = set_html_elem_title_prop(tmphtml, jsonstr.destlist[i].disk_sn);
                target_div.find('select').append(tmphtml);
            }
        }

        var _ul = target_div.find('ul');
        var in_vols = item.vols.include_vols;
        var is_disk_never_backup = true;
        var ex_vols = item.vols.exclude_vols;

        // 将磁盘对应的卷写到 全局变量中
        disk_vol_info[item.id] = in_vols.concat(ex_vols);

        //添加源磁盘,没有排除的卷信息
        for (i = 0; i < in_vols.length; i++) {
            var _html = '<li><input type="checkbox" checked="true" name="current" vol_name="{vol_name}">{display_name}</li>';
            var _disabled_html = '<li><input type="checkbox" checked="true" disabled="disabled" name="current" vol_name="{vol_name}">{display_name}</li>';
            _html = _html.replace('{vol_name}', in_vols[i].VolumeName);
            _html = _html.replace('{display_name}', in_vols[i].display_name);
            _disabled_html = _disabled_html.replace('{vol_name}', in_vols[i].VolumeName);
            _disabled_html = _disabled_html.replace('{display_name}', in_vols[i].display_name);

            if (in_vols[i].disabled) {
                _ul.append(_disabled_html);
            }
            else {
                _ul.append(_html);
            }
        }
        //添加源磁盘,排除的卷信息
        for (i = 0; i < ex_vols.length; i++) {
            if (ex_vols[i].postfix == 'NAN') {
                var _html = '<li><input type="checkbox" disabled="true" vol_name="{vol_name}">{display_name}(从未备份过)</li>';
                _html = _html.replace('{vol_name}', ex_vols[i].VolumeName);
                _html = _html.replace('{display_name}', ex_vols[i].display_name);
            }
            else {
                is_disk_never_backup = false;
                var _html = '<li ><input type="checkbox" vol_name="{vol_name}">{display_name}(<span class="li_warning">历史备份时间：{time_str}</span>)</li>';
                _html = _html.replace('{display_name}', ex_vols[i].display_name);
                _html = _html.replace('{time_str}', ex_vols[i].postfix);
                _html = _html.replace('{vol_name}', ex_vols[i].VolumeName);
            }
            _ul.append(_html);
        }
        // 源磁盘一个分区都没有备份过，禁用它
        //if (!item.vols.include_vols.length && is_disk_never_backup){
        //    target_div.find('.header_check').prop('checked', false).prop('disabled', true);
        //}

        // 隐藏科力锐启动盘
        if (item.clw_disk) {
            target_div.hide();
            target_div.attr('data-clw-disk', '1');
        }

        $('div#HardDisks').append(target_div);
    });
}

function GetClwDiskCount(disks)
{
	var clw_disk_count = 0;
	for(var i=0;i<disks.length;i++)
	{
		if(disks[i].clw_disk)
		{
			clw_disk_count++;
		}
	}

	return clw_disk_count;
}


function verify_disks() {
	var disks = new Array();
    var ex_vols = new Array();
    var boot_vols = new Array();
    var user_checked_disks = $('#HardDisks .header_check:checked');
    if (!user_checked_disks.length) {
        openErrorDialog({title: '错误', html: '请选择需要还原的磁盘。'});
        return false;
    }
	for (var i = 0; i < user_checked_disks.length; i++) {
        var div_container = $(user_checked_disks[i]).parents('.disk_table_container');
        var check_vols = $(div_container).find('.disk_item_body input:checked');
        // 去掉还原磁盘 必选选择卷的限制
        //if ($(div_container).find('.disk_item_body input').length && !check_vols.length){
        //    openErrorDialog({title:'错误',html:'选择磁盘后，请勾选择需要还原的卷。'});
        //    return;
        //}
        var diskjson = {};
        var src = $(div_container).find('.header_check').val().split('|');
		var clw_disk = $(div_container).find('.header_check').hasClass('clw_disk');
		if(clw_disk && !$('#use_clw_boot').prop('checked'))
		{
			//没有勾选使用科力锐启动重定向技术
			continue;
		}
        var dest = $(div_container).find('select').val().split('|');
        var a = new Num64(src[1]);
        var b = new Num64(dest[1]);
        if (a.compare(b) == 1) {
            var disk = {
                ident: src[0],
                src_name: $(div_container).find('.disk_name').text(),
                dst_name: $(div_container).find('select option:selected').text(),
                value: ((parseFloat(src[1]) - parseFloat(dest[1])) / Math.pow(1024, 3)).toFixed(2) + 'GB'
            }
            var error = disk_checker.check(disk);
            if (error) {
                openWarningDialog({title: '警告', html: error, width: 550, height: 250});
                return false;
            }
        }
        diskjson.src = src[0];
        diskjson.dest = dest[0];
		diskjson.clw_disk = clw_disk;
        disks.push(diskjson);
        var all_inputs = $(div_container).find('.disk_item_body input');
        $.each(all_inputs, function (index, item) {
            if (!$(item).is(':checked')) {
                ex_vols.push(disk_vol_info[src[0]][index]);
            }
            else if (disk_vol_info[src[0]][index].display_name.indexOf('/boot') == 0) {
                boot_vols.push(disk_vol_info[src[0]][index]);
            }
        });

    }
	var destdisk = new Array();
    for (var i = 0; i < disks.length; i++) {
        destdisk.push(disks[i].dest);
    }
	var clw_disk_count = GetClwDiskCount(disks);
	if($('#use_clw_boot').prop('checked'))
	{
		if(clw_disk_count==0)
		{
			openErrorDialog({title: '错误', html: '请选择一个科力锐系统加载盘'});
			return false;
		}
		else if(clw_disk_count>1)
		{
			openErrorDialog({title: '错误', html: '只能选择一个科力锐系统加载盘'});
			return false;
		}
	}

    if (isRepeat(destdisk)) {
        openErrorDialog({title: '错误', html: '多个源硬盘不能恢复到同一目标硬盘'});
        return false;
    }
	return {disks: disks, ex_vols: ex_vols, boot_vols: boot_vols};
}

$('#change_disk').change(function (){
	if ($(this).is(':checked')) {
		$('#HardDisks').hide();
		$('#ChangeHardDisks').show();
		InitChangeHardDisk('network');
	}
	else
	{
		$('#HardDisks').show();
		$('#ChangeHardDisks').hide();
	}
});