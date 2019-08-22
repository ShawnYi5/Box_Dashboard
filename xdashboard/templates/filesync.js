function CreateBackupCallback(jsonstr, params) {
    if (jsonstr.r != 0) {
        try {
            var new_url = '../filesync/?a=getPlanList';
            $('#list').setGridParam({url: new_url});
            $('#list').trigger("reloadGrid", [{page: 1}]);
        } catch (e) {
        }
        openErrorDialog({title: '错误', html: jsonstr.e});
        return;
    }
    InitBackupControlData();
    try {
        $("#newbackupFormDiv").dialog('close');
    } catch (e) {
    }
    try {
        var new_url = '../filesync/?a=getPlanList';
        $('#list').setGridParam({url: new_url});
        $('#list').trigger("reloadGrid", [{page: 1}]);
    } catch (e) {
    }
    $('#navigation').html('<div class="font_navigation">导出计划管理</div>');
    if (params.is_change) {
        var html = '更改导出计划成功，您可在<a href="../backupfileexport" style="color:blue;">导出计划管理</a>中查看已更改的计划任务。';
    } else {
        if (params.is_immediately) {
            var html = '新建导出计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况。';
        } else {
            var html = '新建导出计划成功，您可在<a href="../backupfileexport" style="color:blue;">导出计划管理</a>中查看已创建的计划任务。';
        }
    }
    openSuccessDialog({title: '完成', html: html});
}

function hideBackupControl() {
    $('#RefreshSrcServer').hide();
    $("input[name=bakschedule]").prop("disabled", false);
}

function showBackupControl() {
    $('#RefreshSrcServer').show();
    $("input[name=bakschedule]").prop("disabled", false);
}

function InitBackupControlData() {
    $("#tabs").tabs("option", "active", 0);
    $('#host1-detail-container').empty();
    $('#host2-detail-container').empty();
    $('#host-detail-template div').clone().appendTo($('#host1-detail-container'));
    $('#host-detail-template div').clone().appendTo($('#host2-detail-container'));
    $('#taskid').val('');
    $("#sel_storagedevice").val('');
    if (!isInEdit()) {
        $('#taskname').val('');
    }
    $('#stime0').val() || $('#stime0').val(curTime);
    $('#stime1').val() || $('#stime1').val(curTime);
    $('#stime2').val() || $('#stime2').val(curTime);
    $('#stime3').val() || $('#stime3').val(curTime);
    $('#stime4').val() || $('#stime4').val(curTime);
    $("div.myoneday").removeClass("myonedayselected");
    $(".prev").hide();
    $('#next').attr("value", "下一步»");
    $("input[name=bakschedule][value=2]").click();
    $('#bakschedule3').hide();
    $('#bakschedule4').hide();
    $('#bakschedule5').hide();
    $('#usemaxbandwidth').prop('checked', true);
    $('#maxbandwidth').val(300);

    $('#enable-backup-retry').prop('checked', true);
    $('#retry-counts').val(5);
    $('#retry-interval').val(10);
    $('.sync-files').empty();   // #5563 新建计划和更改计划页面初始化
    $('#disk-syncs-template>div').clone().appendTo('.sync-files');
    $(".sync-files .disk_input").prop("disabled",true);
	$(".sync-files .vdisk_input").prop("disabled",true);
	$("#sync_destination").val("");

}

function verify_interval_day_hour_min(interval) {
    interval = parseInt(interval);
    var unit = $('#interval-unit').find(':selected').val();
    if (unit === 'day') {
        if (interval > 365 || interval < 1) {
            return '请输入正确的间隔天数(1-365)'
        }
    } else if (unit === 'hour') {
        if (interval < 1) {
            return '请输入正确的间隔小时(最小1小时)'
        }
    } else if (unit === 'min') {
        if (interval < 5) {
            return '请输入正确的间隔分钟(最小5分钟)'
        }
    } else {
        return '单位错误: ' + unit
    }

    return 'ok';
}

// 点击下一步时， 校验数据
function CheckData(selindex) {
    if (selindex == 0) {
        var serverid = getaciTreeChecked('Servers_Tree1');
        if (serverid == '') {
            openErrorDialog({title: '错误', html: '请选择导出源客户端。'});
            return false;
        }
    } else if (selindex == 2) {
        var serverid = getaciTreeChecked('Servers_Tree2');
        if (serverid == '') {
            openErrorDialog({title: '错误', html: '请选择目标客户端。'});
            return false;
        }
        var sync_destination_value = $('#sync_destination').val();
        if (sync_destination_value == '') {
            tmphtml = '归档存储盘符不能为空。';
            openErrorDialog({title: '错误', html: tmphtml});
            return false;
        }
    } else if (selindex == 3) {
        var taskname = $('#taskname').val();
        if (taskname == '') {
            tmphtml = '导出任务名称不能为空。';
            openErrorDialog({title: '错误', html: tmphtml});
            return false;
        }
        if (taskname.length >= 50) {
            tmphtml = '导出任务名称长度不能超过50个字符。';
            openErrorDialog({title: '错误', html: tmphtml});
            return false;
        }
        var schedule = "";
        var backupschedule = $("input[name='bakschedule']:checked").val();
        $('#confirm_cdpperiod').html('-');
        $('#confirm_cdptype').html('-');
        if (backupschedule == 2) {
            if ($("#stime1").val() == '') {
                openErrorDialog({title: '错误', html: '请选择开始时间。'});
                return false;
            }
        } else if (backupschedule == 3) {
            if ($("#stime2").val() == '') {
                openErrorDialog({title: '错误', html: '请选择开始时间。'});
                return false;
            }
            if ($("#timeinterval").val() == '') {
                openErrorDialog({title: '错误', html: '请选择间隔时间。'});
                return false;
            }
            if (!isNum($("#timeinterval").val())) {
                openErrorDialog({title: '错误', html: '请输入正确的间隔时间, 正整数。'});
                return false;
            }

            var msg = verify_interval_day_hour_min($("#timeinterval").val());
            if (msg !== 'ok') {
                openErrorDialog({title: '错误', html: msg});
                return false;
            }
        } else if (backupschedule == 4) {
            if ($("#stime3").val() == '') {
                openErrorDialog({title: '错误', html: '请选择开始时间。'});
                return false;
            }

            if ($("input[name='perweek']:checked").length == 0) {
                openErrorDialog({title: '错误', html: '请勾选每周几导出。'});
                return false;
            }

        } else if (backupschedule == 5) {
            if ($("#stime4").val() == '') {
                openErrorDialog({title: '错误', html: '请选择开始时间。'});
                return false;
            }
            if ($("div.myoneday.myonedayselected").length == 0) {
                openErrorDialog({title: '错误', html: '请勾选每月第几日导出。'});
                return false;
            }
        }
    } else if (selindex == 1) {   // source参数的校验
        if ($("#disk_checkbox ").prop("checked")) {
            var normal_disk_directory_list = $('.normal_disk_directory_input')
            var flag = true
            normal_disk_directory_list.each(function (i, normal_disk_directory) {
                if (!$(normal_disk_directory).val()) {
                    openErrorDialog({title: '错误', html: '规则不能输入为空。'});
                    flag = false;
                    return false;
                }
            });
            if (!flag) {
                return false;
            }
        }
        if ($("#vdisk_checkbox ").prop("checked")) {
            var vdisk_directory_list = $('#vdisk_complete_path_copy .vdisk_directory_input')
            var flag = true
            vdisk_directory_list.each(function (i, vdisk_directory) {
                if (!$(vdisk_directory).val()) {
                    openErrorDialog({title: '错误', html: '规则不能输入为空。'});
                    flag = false;
                    return false;
                }
                ;
            });
            var vdisk_partition_list = $('#vdisk_complete_path_copy .vdisk_partition_input');
            vdisk_partition_list.each(function (i, partition) {
                if (!$(partition).val()) {
                    openErrorDialog({title: '错误', html: '规则不能输入为空。'});
                    flag = false;
                    return false;
                }
                ;
            });

            var vdisk_data_director_list = $('#vdisk_complete_path_copy .vdisk_data_directory_input');
            vdisk_data_director_list.each(function (i, data_director) {
                if (!$(data_director).val()) {
                    openErrorDialog({title: '错误', html: '规则不能输入为空。'});
                    flag = false;
                    return false;
                }
                ;
            });

            if (!flag) {
                return false;
            }
        }
    }
    return true;
}
// #5563 归档source参数校验


function extract_sync_parameter() {
//sync_rules 新参数整合开始
    var vdisks = [];  //--> 此列表内部为 dict,其中dict内部为两个键值，第二个键的值为一个两层列表
    var sync_source = [];  //-->单纯为list
    var sync_destination = $("#sync_destination").val();

    if ($(".sync-files #disk_checkbox").prop("checked")) {
        var normal_disk_directory_list = $('.sync-files #normal_disk_directory_input');
        normal_disk_directory_list.each(function (i, normal_disk_directory) {
            var normal_disk_directory_str = $(normal_disk_directory).val();
            sync_source.unshift(normal_disk_directory_str);
        });
    }

    if ($(".sync-files #vdisk_checkbox").prop("checked")) {
        var drive_letter_selection_list = [];
        drive_letter_selection_list.unshift("K", "L", "M", "N", "O", "P"); //给各分区分配的盘符列表
        var vdisk_directory_list = $('.sync-files .vdisk_complete_path_copy');  //获取vdisk块的div列表
        vdisk_directory_list.each(function (i, vdisk_directory) {
                var vdisk_directory_input = $(vdisk_directory).find('.vdisk_directory_input')[0];
                var vdisk_directory_str = $(vdisk_directory_input).val();
                var vdisk_inner_dict = {}; //该字典含有两元素：fild_vhd和var part_num_and_drive_letter_list
                var part_num_and_drive_letter_list = new Array();
                vdisk_inner_dict["file_vhd"] = vdisk_directory_str; //生成列表vdisks内元素A的字典file_vhd

                var vdisk_partition_list = $(vdisk_directory).find(".vdisk_partition");
                vdisk_partition_list.each(function (j, vdisk_partition) {
                    var part_num_and_drive_letter = new Array();
                    var vdisk_partition_input = $(vdisk_partition).find(".vdisk_partition_input")[0];
                    var part_num = parseInt($(vdisk_partition_input).val());
                    var drive_letter = drive_letter_selection_list.pop();  //给该分区分配盘符
                    part_num_and_drive_letter.unshift(part_num, drive_letter);
                    part_num_and_drive_letter_list.unshift(part_num_and_drive_letter); //e.g.生成[[1,P]]

                    var vdisk_data_directory_list = $(vdisk_partition).find(".vdisk_data_directory_input");
                    vdisk_data_directory_list.each(function (k, vdisk_data_directory) {
                        var data_directory = new Array();
                        data_directory.unshift(drive_letter, ":", $(vdisk_data_directory).val());
                        var data_derectory_str = data_directory.join("");
                        sync_source.unshift(data_derectory_str); //e.g. sync_source=[P:/source_dir]
                    });

                });
                vdisk_inner_dict["part_num_and_drive_letter_list"] = part_num_and_drive_letter_list;  //生成列表vdisks内元素A的字典file_vhd
                vdisks.unshift(vdisk_inner_dict);  // vdisk=[{"file_vhd":"C:/soure_file1.vhdx","part_num_and_drive_letter_list":"[[1,P]]"}]
            }
        );
    }

    var sync_rules_dict = {};
    sync_rules_dict["aio_ip"] = $('#ipOptions').val();
    sync_rules_dict["vdisks"] = vdisks;   //vdisks 为 列表/
    sync_rules_dict["sync_source"] = sync_source;  //sync_source 为 列表/
    sync_rules_dict["sync_destination"] = sync_destination;

    var sync_rules_json_str = JSON.stringify(sync_rules_dict);
    //sync_rules 新参数整合完毕
    return sync_rules_json_str;
}

// 创建计划完成，并提交参数到后台
function OnFinish(other_params) {
    var postdata = 'taskname=' + encodeURIComponent($("#taskname").val());
    postdata += '&source_host_ident=' + getaciTreeChecked('Servers_Tree1');
    postdata += '&target_host_ident=' + getaciTreeChecked('Servers_Tree2');
    var sync_rules_json_str = extract_sync_parameter();
    postdata += '&sync_rules=' + sync_rules_json_str;   //  #5563把用户输入的输入框值 组织成 以前的格式
    var backupschedule = $("input[name='bakschedule']:checked").val();
    postdata += '&bakschedule=' + backupschedule;
    if (backupschedule == 2) {
        //仅导出一次
        postdata += '&starttime=' + $("#stime1").val();
    } else if (backupschedule == 3) {
        //每天
        postdata += '&starttime=' + $("#stime2").val();
        postdata += '&timeinterval=' + $("#timeinterval").val();
    } else if (backupschedule == 4) {
        //每周
        postdata += '&starttime=' + $("#stime3").val();
        postdata += "&" + $("input[name='perweek']").serialize();
    } else if (backupschedule == 5) {
        //每月
        postdata += '&starttime=' + $("#stime4").val();
        for (var i = 0; i < $("div.myoneday.myonedayselected").length; i++) {
            var monthly = $("div.myoneday.myonedayselected")[i].innerHTML;
            postdata += '&monthly=' + monthly;
        }
    }
    postdata += '&intervalUnit=' + $('#interval-unit :selected').val();
    // 其他的参数: 脚本信息
    postdata += other_params;

    if ($('#taskid').val()) {
        postdata += '&taskid=' + $('#taskid').val();
        openConfirmDialog({
            title: '确认信息',
            html: '你确定要更改此项计划吗?',
            onBeforeOK: function () {
                postdata += "&a=createModifyPlan&immediately=0";
                myAjaxPost('../filesync_handle/', postdata, CreateBackupCallback, {
                    'is_change': true,
                    'is_immediately': false
                });
                $(this).dialog('close');
                $("#newbackupFormDiv").dialog('destroy');
            }
        });
        return;
    }

    $("#finishFrom").attr('title', '完成').dialog({
        autoOpen: true,
        height: 200,
        width: 400,
        modal: true,
        buttons: {
            '完成设置': function () {
                postdata += "&a=createModifyPlan&immediately=0";
                myAjaxPost('../filesync_handle/', postdata, CreateBackupCallback, {
                    'is_change': false,
                    'is_immediately': false
                });
                $(this).dialog('close');
                $("#newbackupFormDiv").dialog('destroy');
            },
            '立即导出': function () {
                postdata += "&a=createModifyPlan&immediately=1";
                myAjaxPost('../filesync_handle/', postdata, CreateBackupCallback, {
                    'is_change': false,
                    'is_immediately': true
                });
                $(this).dialog('close');
                $("#newbackupFormDiv").dialog('destroy');
            }
        }
    });
}

function setPreControlStatus(selindex) {
    if (selindex == 1) {
        $(".prev").hide();
    }
    if (selindex == 2 && $('#uitasktype').val() == 'uitaskpolicy') {
        $(".prev").hide();
    }
    $('#next').attr("value", "下一步»");
}

function setControlStatus(selindex) {
    if (selindex == 0) {
    } else if (selindex == 2) {
        var backupschedule = $("input[name='bakschedule']:checked").val();
        if (backupschedule == 1) {
            $('#cdpperiod').attr("disabled", false);
            $("input[name='cdptype']").attr("disabled", false);
        } else {
            $('#cdpperiod').attr("disabled", true);
            $("input[name='cdptype']").attr("disabled", true);
        }
        var hostIdent = getaciTreeChecked('Servers_Tree1');
        // 新建策略时
        if (hostIdent == '') {
            $('label[for=advanced2-manage]').hide();
        }
    } else if (selindex == 3) // 确认参数界面
    {
        $('#next').val('完成');
        var taskname = $('#taskname').val();
        var storagedevice = $("#storagedevice").find("option:selected").text();
        var backuptype = 1;
        $('#confirm_taskname').html(taskname);
        $('#confirm_storagedevice').html(storagedevice);
        $('#confirm_backuptype').html('整机导出');

        var servername = getaciTreeNameChecked('Servers_Tree1');
        $('#confirm_src').html(servername);
        $('#confirm_dest').html(getaciTreeNameChecked('Servers_Tree2'));
        var schedule = "";
        var backupschedule = $("input[name='bakschedule']:checked").val();
        $('#confirm_cdpperiod').html('-');
        $('#confirm_cdptype').html('-');
        if (backupschedule == 2) {
            schedule = '仅导出一次，开始时间：' + $("#stime1").val();
        } else if (backupschedule == 3) {
            schedule = '按间隔时间，开始时间：' + $("#stime2").val() + '，每' + $("#timeinterval").val() + '{0}开始执行';
            schedule = schedule.replace('{0}', $('#interval-unit :selected').text());
        } else if (backupschedule == 4) {
            var tmp = new Array();
            for (var i = 0; i < $("input[name='perweek']:checked").length; i++) {
                var n = parseInt($("input[name='perweek']:checked")[i].value);
                switch (n) {
                    case 1:
                        tmp.push("星期一");
                        break;
                    case 2:
                        tmp.push("星期二");
                        break;
                    case 3:
                        tmp.push("星期三");
                        break;
                    case 4:
                        tmp.push("星期四");
                        break;
                    case 5:
                        tmp.push("星期五");
                        break;
                    case 6:
                        tmp.push("星期六");
                        break;
                    case 7:
                        tmp.push("星期日");
                        break;
                }
            }
            schedule = '每周，开始时间：' + $("#stime3").val() + '，每周' + tmp.join('、') + "导出";
        } else if (backupschedule == 5) {
            var tmp = new Array();
            for (var i = 0; i < $("div.myoneday.myonedayselected").length; i++) {
                tmp.push($("div.myoneday.myonedayselected")[i].innerHTML);
            }
            schedule = '每月，开始时间：' + $("#stime4").val() + '，每月' + tmp.join(',') + "日导出";
        }
        $('#confirm_schedule').html(schedule);
        var sync_rules = JSON.parse(extract_sync_parameter()); // #5563 文件归档参数展示在最后的确认框
        $('#confirm_dir_rules').html(DisplaySorttedArchiveParams(sync_rules));
        if (get_data_keeps_deadline_unit() === 'day') {
            var retentionperiod = $("#retentionperiod").val() + '天';
        } else {
            var retentionperiod = $("#retentionperiod").val() + '个月';
        }

        $('#confirm_retentionperiod').html(retentionperiod);
        var cleandata = $("#cleandata").val() + 'GB';
        $('#confirm_cleandata').html(cleandata);
        var maxbandwidth = $("#maxbandwidth").val();
        $('#confirm_maxbandwidth').html('无限制');
        var usemaxbandwidth = 1;
        if ($('#usemaxbandwidth').prop("checked")) {
            usemaxbandwidth = 0;
            $('#confirm_maxbandwidth').html(maxbandwidth + "Mbit/s");
        }

        var vmware_tranport_modes = $("input[name='vmware_tranport_modes']:checked").val();
        switch (vmware_tranport_modes) {
            case '1':
                $('#confirm_vmware_tranport_modes').html('自动');
                break;
            case '2':
                $('#confirm_vmware_tranport_modes').html('SAN');
                break;
            case '3':
                $('#confirm_vmware_tranport_modes').html('HotAdd');
                break;
            case '4':
                $('#confirm_vmware_tranport_modes').html('NBD');
                break;
        }

        var vmware_quiesce = $('#vmware_quiesce').prop("checked") ? '1' : '0';
        if (vmware_quiesce == '1') {
            $('#confirm_vmware_quiesce').html('是');
        } else {
            $('#confirm_vmware_quiesce').html('否');
        }

        $('#confirm_encipher').html($('#isencipher').val());
        var backupmode = $('#full-bak-everytime').prop("checked") ? '1' : '2';
        switch (backupmode) {
            case '1':
                $('#confirm_backupmode').html('每次都完整导出');
                break;
            case '2':
                $('#confirm_backupmode').html('仅第一次进行完整导出，以后增量导出');
                break;
        }
        if ($('#isencipher').is(':checked')) {
            $('#confirm_isencipher').html('加密');
        } else {
            $('#confirm_isencipher').html('不加密');
        }
        if ($('#iscompress').is(':checked')) {
            $('#confirm_iscompress').html('压缩');
        } else {
            $('#confirm_iscompress').html('不压缩');
        }
        var full_interval = $('#full_interval').val();
        if (full_interval == -1) {
            $('#confirm_full_interval').html('仅第一次进行完整导出，以后增量导出');
        } else if (full_interval == 0) {
            $('#confirm_full_interval').html('每次都完整导出');
        } else {
            $('#confirm_full_interval').html('间隔' + full_interval + '次，进行一次完整导出');
        }

        var keepingpoint = parseInt($('#keepingpoint').val());
        $('#confirm_keepPointNum').text(keepingpoint + '个');
        $('#confirm_dupFolder').text($("#dup-sys-folder").prop("checked") ? '是' : '否');

        // 导出重试
        var enable_backup_retry = $('#enable-backup-retry').is(':checked'),
            backup_retry_count = $('#retry-counts').val(),
            backup_retry_interval = $('#retry-interval').val();
        if (enable_backup_retry) {
            $('#confirm_backup_retry').text('启用；重试间隔：' + backup_retry_interval + '分钟；重试次数：' + backup_retry_count + '次');
        } else {
            $('#confirm_backup_retry').text('禁用');
        }
        if (!is_norm_plan()) {
            $('#confirm_backup_retry').text('-');
        }

        // 线程信息
        $('#confirm_thread_count').text(set_get_thread_count());
    }
    if (selindex == 4) {
        OnFinish('');
    }
}

$('#prev').click(function () {
    var selindex = $("#tabs").tabs('option', 'active');
    $("#tabs").tabs("option", "active", selindex - 1);
    setPreControlStatus(selindex);
});

function my_next(selindex) {
    $("#tabs").tabs("option", "active", selindex + 1);
    setControlStatus(selindex);
    if (selindex == 0) {
        $(".prev").show();
    }

    if (selindex == 2 && $('#uitasktype').val() == 'uitaskpolicy') {
        $(".prev").show();
    }

    if (selindex == 3 && $('#uitasktype').val() == 'uitaskpolicy') {
        $('.next').trigger('click');
    }
}

$('#next').click(function () {
    var selindex = $("#tabs").tabs('option', 'active');
    var id = getaciTreeChecked('Servers_Tree1');
    var agent_type = GetAciTreeValueRadio('Servers_Tree1', id, 'type');
    if (agent_type == 2) {
        //免代理或策略
        $('#shell_info_tr_id').hide();
    } else {
        $('#shell_info_tr_id').show();
    }
    if (selindex == 0) {
        $('')
        var hostName = getaciTreeNameChecked('Servers_Tree1');
        var planName = '文件导出' + hostName + CurentTime();
        if (!isInEdit()) {
            $('#taskname').val(planName);
        }
        $('.vmware_div').hide();
        $('#security-manage').show();
        $('#security-manage-lab').text('▼');
        $('#navigation').html('<div class="font_navigation">导出 > 新建导出计划 > 导出信息</div>');
    } else if (selindex == 1) {
        $('#navigation').html('<div class="font_navigation">导出 > 新建导出计划 > 导出计划</div>');
        $('#stime0').val() || $('#stime0').val(curTime);
        $('#stime1').val() || $('#stime1').val(curTime);
        $('#stime2').val() || $('#stime2').val(curTime);
        $('#stime3').val() || $('#stime3').val(curTime);
        $('#stime4').val() || $('#stime4').val(curTime);
    } else if (selindex == 2) {
        $('#navigation').html('<div class="font_navigation">导出 > 新建导出计划 > 其他设置</div>');
        if (is_norm_plan()) {
            retry_item_enable();
        } else {
            retry_item_disable();
        }

        if (!isInEdit()) {
            if ($('#uitasktype').val() != 'uitaskpolicy') {
                set_data_keeps_deadline('1');
                set_data_keeps_deadline_unit('month');
                set_get_thread_count('4');
            }
        }
        set_retentionperiod_msg(false);
    } else if (selindex == 3) {
        $('#navigation').html('<div class="font_navigation">导出 > 新建导出计划 > 执行脚本</div>');
    }


    if (CheckData(selindex)) {
        if (selindex == 3 && agent_type != 2) {
            var maxbandwidth = $('#maxbandwidth').val(),
                BackupIOPercentage = $('#BackupIOPercentage').val(),
                warning_str = '';
            var msg = '<p style="text-indent: 2em">当前配置<span style="color: red">[{warning_str}]</span>在导出时资源占用可能过大，' +
                '可能会引起导出源主机上业务系统出现响应缓慢或超时等问题，请确认是否有这样的风险，没有风险请输入<span style="color: red">OK</span>并点击确认按钮。</p>' +
                '<input type="text" id="confirm_input" style="width: 100%" onblur="removespace(this)">' +
                '<p style="color: grey">提示：导出过程中也可以调整资源占用。</p>'
            if (maxbandwidth > 300) {
                warning_str += '(占用源主机的网络带宽超过300Mbit/s)';
            }
            if (maxbandwidth == -1) {
                warning_str += ' (占用源主机的网络带宽为 不限制)';
            }
            if (BackupIOPercentage > 30) {
                warning_str += ' (占用源主机的存储性能的百分比超过30%)';
            }
            if (warning_str) {
                openConfirmDialog({
                    html: msg.replace('{warning_str}', warning_str),
                    width: 580,
                    height: 'auto',
                    onBeforeOK: function () {
                        if ($('#confirm_input').val().toLowerCase() == 'ok') {
                            my_next(selindex);
                            $(this).dialog('close');
                        } else {
                            return;
                        }
                    }
                })
                return;
            }
        }
        my_next(selindex);
    }
});


function Getstoragedevice(retjson) {
    $("#storagedevice").empty();
    $.each(retjson, function (i, item) {
        var free_GB = (item.free / Math.pow(1024, 1)).toFixed(2);
        free_GB = (item.value == -1) ? 0 : free_GB;

        var html = "（可用：{3}）";
        if (free_GB == 0) {
            html = ''
        } else {
            html = html.replace('{3}', free_GB + 'GB');
        }

        if ($('#sel_storagedevice').val() == item.value) {
            $("#storagedevice").append("<option value='" + item.value + "'  selected=\"selected\" >" + item.name + "</option>");
        } else {
            $("#storagedevice").append("<option value='" + item.value + "'>" + item.name + html + "</option>");
        }
    });
}


$("input[name='bakschedule']").click(function () {
    var choice = this.value;
    $.each([2, 3, 4, 5], function (_, v) {
        if (v == choice) {
            $('#bakschedule' + v).slideDown(600);
        } else {
            $('#bakschedule' + v).slideUp(600);
        }
    });
});

$('#RefreshSrcServer').button().click(function () {
    RefreshAciTree("Servers_Tree1", '../backup_handle/?a=getlist&inc_offline=1&inc_nas_host=1&id=');
});

$('#RefreshSrcServer2').button().click(function () {
    RefreshAciTree("Servers_Tree2", '../backup_handle/?a=getlist&inc_offline=1&id=');
});

function GetServerInfoCallback(jsonstr, div_name) {
    if (jsonstr.r != 0) {
        $(div_name + ' .show_servername').html(jsonstr.e);
        return;
    }
    $(div_name + ' .show_servername').html(jsonstr.servername);
    $(div_name + ' .show_pcname').html(jsonstr.pcname);
    $(div_name + ' .show_ip').html(jsonstr.ip);
    $(div_name + ' .show_mac').html(jsonstr.mac);
    $(div_name + ' .show_os').html(jsonstr.os);
    $(div_name + ' .show_buildnum').html(jsonstr.buildnum);
    $(div_name + ' .show_harddisknum').html(jsonstr.harddisknum);
    $(div_name + ' .show_harddiskinfo').html(jsonstr.harddiskinfo);
    $(div_name + ' .show_total').html(jsonstr.total + 'GB');
    $(div_name + ' .show_use').html(jsonstr.use + 'GB');
    $(div_name + ' .network_transmission_type').val(jsonstr.network_transmission_type);

    var host_ext_info = JSON.parse(jsonstr.host_ext_info);
    if (host_ext_info.nas_path) {
        $(div_name + ' .nas_table_info').show();
        $(div_name + ' .normal_table_info').hide();
        $(div_name + ' .show_nas_servername').html(jsonstr.servername);
        $(div_name + ' .show_nas_path').html(host_ext_info.nas_path);
        if (isNFSpath(host_ext_info.nas_path)) {
            $(div_name + ' .show_nas_protocol').html('NFS');
        } else {
            $(div_name + ' .show_nas_protocol').html('CIFS');
        }
    } else {
        $(div_name + ' .nas_table_info').hide();
        $(div_name + ' .normal_table_info').show();
    }
}

// 获取主机信息
$('#Servers_Tree1').on('acitree', function (event, api, item, eventName, options) {
    if (eventName === 'opened') {
        var hostName = $.cookie('default_host_name_when_create_plan');
        if (hostName) {
            CheckAciTreeRadioByLabel('Servers_Tree1', hostName);
            $.cookie('default_host_name_when_create_plan', null, {path: '/'});
        }
    }
    if (eventName != 'selected') {
        return;
    }
    var id = api.getId(item);
    if (id != undefined && id.substring(0, 3) != 'ui_') {
        $('#host1-detail-container .host-detail-item').empty();
        var params = "a=getserverinfo&id=" + id;
        myAjaxGet('../backup_handle/', params, GetServerInfoCallback, '#host1-detail-container');
    }
});

// 获取主机信息
$('#Servers_Tree2').on('acitree', function (event, api, item, eventName, options) {
    if (eventName != 'selected') {
        return;
    }
    var id = api.getId(item);
    if (id != undefined && id.substring(0, 3) != 'ui_') {
        $('#host2-detail-container .host-detail-item').empty();
        var params = "a=getserverinfo&id=" + id;
        myAjaxGet('../backup_handle/', params, GetServerInfoCallback, '#host2-detail-container');
    }
});

function setmaxbandwidthstatus() {
    if ($('#usemaxbandwidth').prop('checked')) {
        $('#maxbandwidth').prop('disabled', false);
    } else {
        $('#maxbandwidth').prop('disabled', true);
    }
}

$('#usemaxbandwidth').click(function () {
    setmaxbandwidthstatus();
});

// 后台获取策略数据，回调方法
function GetpolicydetailCallback(jsonstr) {
    if (jsonstr.r != 0) {
        openErrorDialog({title: '错误', html: jsonstr.e});
        return;
    }
    switch (jsonstr.cycletype) {
        case 2:
            $($("input[name=bakschedule]")[1]).click();
            $("#stime1").val(jsonstr.starttime);
            break;
        case 3:
            $($("input[name=bakschedule]")[2]).click();
            $("#stime2").val(jsonstr.starttime);
            $("#timeinterval").val(jsonstr.timeinterval);
            $('#interval-unit').val(jsonstr.unit);
            break;
        case 4:
            $($("input[name=bakschedule]")[3]).click();
            $("#stime3").val(jsonstr.starttime);
            var pes = jsonstr.period.split(',');
            for (var i = 0; i < pes.length; i++) {
                $($("input[name=perweek]")[pes[i] - 1]).prop("checked", true);
            }
            break;
        case 5:
            $($("input[name=bakschedule]")[4]).click();
            $("#stime4").val(jsonstr.starttime);
            var pes = jsonstr.monthly.split(',');
            for (var i = 0; i < pes.length; i++) {
                for (var j = 0; j < $("div.myoneday").length; j++) {
                    var monthly = $("div.myoneday")[j].innerHTML;
                    if (monthly == pes[i]) {
                        $($("div.myoneday")[j]).addClass("myonedayselected");
                        break;
                    }
                }
            }
            break;
        default:
            $($("input[name=bakschedule]")[0]).click();
            if (jsonstr.starttime) {
                $("#stime0").val(jsonstr.starttime);
            } else {
                $("#stime0").val(curTime);
            }
    }

    if (jsonstr.retentionperiod_unit === 'day') {
        set_data_keeps_deadline_unit('day');
        set_data_keeps_deadline(jsonstr.retentionperiod);
    } else if (jsonstr.retentionperiod_unit === 'month') {
        set_data_keeps_deadline_unit('month');
        set_data_keeps_deadline(jsonstr.retentionperiod / 30);
    } else {
        set_data_keeps_deadline_unit('month');
        set_data_keeps_deadline(jsonstr.retentionperiod / 30);
    }

    set_get_thread_count(jsonstr.thread_count);

    $("#cleandata").val(jsonstr.cleandata);
    if (jsonstr.cycletype == 1) {
        $("#cdpperiod").val(jsonstr.cdpperiod);
    }
    $("#maxbandwidth").val(jsonstr.maxbandwidth);
    if (jsonstr.usemaxbandwidth == 0) {
        $("#maxbandwidth").prop("disabled", true);
        $("#usemaxbandwidth").prop("checked", false);
    } else {
        $("#maxbandwidth").prop("disabled", false);
        $("#usemaxbandwidth").prop("checked", true);
    }
    $('#keepingpoint').val(jsonstr.keepingpoint);
    if (jsonstr.cdptype == 0) {
        $("input[name=cdptype][value=0]").click();
    } else {
        $("input[name=cdptype][value=1]").click();
    }
    if (jsonstr.isencipher == 1) {
        $('#isencipher').val("加密");
    } else {
        $('#isencipher').val("不加密");
    }

    if (jsonstr.backupmode == 1) {
        $("input[name='backupmode'][value=1]").prop("checked", true);
    } else {
        $("input[name='backupmode'][value=2]").prop("checked", true);
    }

    $('#dup-sys-folder').prop('checked', jsonstr.removeDup);
}

// 新建计划，选择导出策略时
$('#backuppolicy').change(function () {
    var id = $(this).children('option:selected').val();
    if (id == -1) {
        return;
    }
    var params = "a=getpolicydetail&id=" + id;
    myAjaxGet('../backup_handle/', params, GetpolicydetailCallback);
});


$(function () {
    $('#bakschedule3').hide();
    $('#bakschedule4').hide();
    $('#bakschedule5').hide();
    $('#bakschedule6').hide();
    for (var i = 1; i <= 31; i++) {
        $('#dayselect').append('<div class="myoneday">' + i + '</div>');
    }

    $("div.myoneday").click(function () {
        if ($(this).hasClass("myonedayselected")) {
            $(this).removeClass("myonedayselected");
        } else {
            $(this).addClass("myonedayselected");
        }
    });
});


String.prototype.endWith = function (endStr) {
    var d = this.length - endStr.length;
    return (d >= 0 && this.lastIndexOf(endStr) == d);
};

function checkParent(api, child_item) {
    var parent = api.parent(child_item);
    if (!api.isChecked(parent)) {
        api.check(parent);
    }
}

// 遍历AciTree，将所有符合child_id的父，选中
function checkParentById(tree_id, child_id) {
    var api = $("#" + tree_id).aciTree('api');
    var childs = api.children(null, true, true);
    $.each(childs, function (index, item) {
        var childNode = $(item);
        if (child_id == api.getId(childNode)) {
            checkParent(api, childNode);
        }
    });
}

var myFunc = function (event, api, item, eventName, options) {
    // 数据读取完成
    if (eventName == 'init') {
        $('#waiting-tree-data').html('');
        var childs = api.children(null, true, true);
        if (api.enabled(childs).length == 0) {
            $('#waiting-tree-data').html('<span style="color: #ff0000">获取磁盘信息失败，不能设置导出区域</span>');
        }
    }

    // 选择、取消子元素: 处理跨盘卷的情况
    if ((eventName == 'checked' || eventName == 'unchecked') && api.level(item) == 1) {
        var checked_id = getAciTreeBoxChecked('disk-vol-tree', true);
        var unchecked_id = getAciTreeBoxChecked('disk-vol-tree', false);
        checked_id = (checked_id == '') ? [] : checked_id.split(',');
        unchecked_id = (unchecked_id == '') ? [] : unchecked_id.split(',');
        var intersection = checked_id.filter(function (cked_id) {
            return $.inArray(cked_id, unchecked_id) > -1;
        });
        intersection = $.unique(intersection);
        if (eventName == 'checked' && intersection.length > 0) {
            $('#disk-vol-tree').off('acitree');
            $.each(intersection, function (index, id) {
                CheckAciTreeBox('disk-vol-tree', id, true);
                checkParentById('disk-vol-tree', id);
            });
            $('#disk-vol-tree').on('acitree', myFunc);
        }
        if (eventName == 'unchecked' && intersection.length > 0) {
            $('#disk-vol-tree').off('acitree');
            $.each(intersection, function (index, id) {
                CheckAciTreeBox('disk-vol-tree', id, false);
            });
            $('#disk-vol-tree').on('acitree', myFunc);
        }
    }
    // 选择子元素：父必选择
    if (eventName == 'checked' && api.level(item) == 1) {
        checkParent(api, item);
    }
    // 取消父元素：有孩子、父为Boot，则禁止取消父
    if (eventName == 'unchecked' && api.level(item) == 0) {
        var isBoot = String(api.getLabel(item)).endWith('(启动盘)');
        $.each(api.children(item), function (index, elem) {
            var child = $(elem);
            if (api.isChecked(child) || isBoot) {
                api.check(item);
                return false;
            }
        });
    }
};

$('#disk-vol-tree').on('acitree', myFunc);


function isInEdit() {
    return window.IdOfChangePlan != undefined;
}

function is_norm_plan() {
    var checked_radio = $('input[type=radio][name=bakschedule]:checked');
    return checked_radio.val() !== '1' && checked_radio.val() !== '2';
}

function retry_item_enable() {
    $('label[for=advanced3-manage]').show();
    $('#the-br-for-retry').show();
    $('#advanced3-manage').hide();
}

function retry_item_disable() {
    $('label[for=advanced3-manage]').hide();
    $('#the-br-for-retry').hide();
    $('#advanced3-manage').hide();
}

function get_data_keeps_deadline() {
    return $('#retentionperiod').val();
}

function set_data_keeps_deadline(val) {
    $('#retentionperiod').val(val);
}

function get_data_keeps_deadline_unit() {
    return $('#retentionperiod-unit').val();
}

function set_data_keeps_deadline_unit(unit) {
    $('#retentionperiod-unit').val(unit);
}

$('#retentionperiod-unit').on('change', function () {
    set_retentionperiod_msg(true);
});

function set_retentionperiod_msg(auto) {
    if ($('#retentionperiod-unit').val() === 'day') {
        if (parseInt($("#tabs-3 .radio:checked").val()) == 1) {
            $('#retentionperiod-msg').text('5-300天');
            $('#retentionperiod').attr({'min': 5, 'max': 300});
            if (auto) {
                set_data_keeps_deadline('5');
            }
        } else {
            $('#retentionperiod-msg').text('3-360天');
            $('#retentionperiod').attr({'min': 3, 'max': 360});
            if (auto) {
                set_data_keeps_deadline('3');
            }
        }
    } else {
        $('#retentionperiod-msg').text('1-240月');
        $('#retentionperiod').attr({'min': 1, 'max': 240});
        if (auto) {
            set_data_keeps_deadline('1');
        }
    }
}

function set_get_thread_count(cnt) {
    if (cnt) {
        $('#thread-count').val(cnt);
    } else {
        return $('#thread-count').val();
    }
}

// 将数据库或者页面提取的json转化为易读形式
function DisplaySorttedArchiveParams(sync_rules) {  //#5563 新增
        var vdisk_msg_list_display =[];
        var disk_msg_list_display =[];
        var vdisk_msg_list = [];
        var disk_msg_list = [];
        var aio_ip = sync_rules.aio_ip;
        var destination = sync_rules.sync_destination;
        var vdisks = sync_rules.vdisks;
        var sources = sync_rules.sync_source;
        var vdisk_sources_list = [];
        var disk_sources_list = sources.slice(0);

        for (i = 0; i < vdisks.length; i++) {
            var vdisk = vdisks[i];
            var vhd_path = vdisk.file_vhd;
            var num_and_letter = vdisk.part_num_and_drive_letter_list;
            var vdisk_part_num_msg_list = [];
            for (j = 0; j < num_and_letter.length; j++) {
                var part_num = num_and_letter[j][0];
                var drive_letter = num_and_letter[j][1];
                for (k = 0; k < sources.length; k++) {
                    var source = sources[k];
                    var vdisk_part_num = [];
                    if (source.charAt(0) === drive_letter) {
                        vdisk_sources_list.push(source);  //将属于vdisk中的source放入一个vdisk_sources_list
                        vdisk_part_num.unshift(vhd_path, "中", part_num, "号分区内的",source.substring(2));
                        var vdisk_part_num_msg = vdisk_part_num.join("");
                        vdisk_part_num_msg_list.push(vdisk_part_num_msg);

                    }
                }
            }
            vdisk_msg_list.push(vdisk_part_num_msg_list);
        }
        // 从sources中剔除vdisk的source，获得disk的source
        for (m = 0; m < vdisk_sources_list.length; m++) {
            var vdisk_source = vdisk_sources_list[m];
            var vdisk_source_index = disk_sources_list.indexOf(vdisk_source);
            disk_sources_list.splice(vdisk_source_index, 1);
        }
        for (n = 0; n < disk_sources_list.length; n++) {
            var disk_source = disk_sources_list[n];
            disk_msg_list.push(disk_source)
        }

        for(i=0;i<vdisk_msg_list.length;i++) {
            var vdisk_msg_list_sub = vdisk_msg_list[i];
            for (j = 0; j < vdisk_msg_list_sub.length; j++) {
                vdisk_msg_list_display.unshift("<div class='archive_div'>", vdisk_msg_list_sub[j], "<div>");
            }
        }
        vdisk_msg_list_display = vdisk_msg_list_display.join("");

        for(i=0;i<disk_msg_list.length;i++){
            disk_msg_list_display.unshift("<div class='archive_div'>",disk_msg_list[i],"<div>");
        }
        disk_msg_list_display = disk_msg_list_display.join("");

        destination = destination+":\\归档";
        var display_list = [];
        display_list.unshift(
            "<div style='font-size:12px;color:#555555;font-family:Tahoma,Arial, Helvetica, sans-serif;'>",
            "<div class='archive_div'>连接的一体机的ip:</div>",
            "<div class='archive_div archive_data' >", aio_ip, "</div>",
            "<div class='archive_div'>源客户端需要归档文件路径:</div>",
            "<div class='archive_data'>",
            disk_msg_list_display,
            vdisk_msg_list_display,
            "</div>",
            "<div class='archive_div' style='margin-left:-2em;'>目标客户端存储归档文件路径:</div>",
            "<div class='archive_div'>", destination, "</div>",
            "</div>",
            );
        var display_str = display_list.join("");
        return display_str;
    }