function has_choice_driver() {
    var driverAray = new Array();

    // 本机还原且同构 可以不勾选任何驱动
    if (is_same() && is_restore_to_self()) {
        return {'r': 0};
    }

    var api = $('#driver_version_tree').aciTree('api');
    var children = api.children(null, false, true);
    for (var i = 0; i < children.length; i++) {
        var version = api.children($(children[i]), false, true),
            valid = false;
        version.each(api.proxy(function (element) {
            var item = $(element);
            if (api.isChecked(item)) {
                valid = true;
                return false;
            }
        }, true));
        if (!valid) {
            driverAray.push(api.getLabel($(children[i])));
        }
    }
    if (driverAray.length) {
        return {'r': 1, 'driverAray': driverAray}
    }
    return {'r': 0};
}

// 响应选择框变化
$('#driver_version').change(function () {
    var choice_name = $(this).val();
    if (choice_name == '') {
        uncheck_all_conditional();
        return;
    }
    var api = $('#driver_version_tree').aciTree('api');
    var children = api.children(null, true, true);
    api.checkboxes(children).each(api.proxy(function (element) {
        var item = $(element),
            name = api.itemData(item).Str_HWPlatform,
            parent = api.parent(item),
            status = name == choice_name;
        if (status) {
            this.check(item);
        } else {
            if (parent && api.itemData(parent).is_platform) {
                if (api.itemData(item).Str_HWPlatform == '用户导入') {
                    // 用户导入驱动不受影响
                } else {
                    this.uncheck(item);
                }
            }
        }
    }, true));
})

// 设备堆栈一致
function is_same() {
    return $('#step2 td:eq(2)').html().indexOf('同构') != -1;
}

// 本机还原 mac地址一致 Linux 没有意义
function is_restore_to_self() {
    return $('#step2').attr('data-restore-to-self') == '1';
}

var g_blk_list = ['用户导入', '', ' ', null]; // 不能作为选择项的黑名单
// 加入驱动版本的筛选框
function append_choice($tree) {
    var api = $tree.aciTree('api'),
        children = api.children(null, true, true),
        Str_HWPlatforms = [];

    $('#driver_version_wrap select option:gt(0)').remove();
    api.checkboxes(children).each(api.proxy(function (element) {
        var item = $(element),
            Str_HWPlatform = api.itemData(item).Str_HWPlatform;
        if (Str_HWPlatforms.indexOf(Str_HWPlatform) == -1 && g_blk_list.indexOf(Str_HWPlatform) == -1) {
            Str_HWPlatforms.push(Str_HWPlatform);
        }
    }, true));
    if (Str_HWPlatforms.length >= 2) {
        $('#driver_version_wrap').show();
        for (var i = 0; i < Str_HWPlatforms.length; i++) {
            var v = Str_HWPlatforms[i],
                op = $('<option></option>').text(v).val(v);
            $('#driver_version_wrap select').append(op);
        }
        uncheck_all_conditional();
    } else if (check_multi_version) {
        check_multi_version = false;
        $('#driver_update_method input[value=1]').click();
        $('#driver_version_wrap').hide();
    }

    // 本机同构 所有都不选
    if (is_same() && is_restore_to_self()) {
        uncheck_all();
    }

}

var check_multi_version = false; //是否需要检测含有多个版本的驱动。
try {
    console.log(g_op_type);
    console.log(g_have_check_dest_host_in_plan);
} catch (e) { // 还原和迁移界面，需要定义一下函数,热备界面不需要重复定义
    $('#tabs').on('tabsbeforeactivate', function (event, ui) {
        var oldTabIndex = ui.oldTab.index(),
            newTabIndex = ui.newTab.index();

        if (oldTabIndex <= 3 && newTabIndex == 4) {
            if (src_is_linux()) {
                $('#driver_update_method input[value=1]').click();
            } else {
                if (is_same() && is_restore_to_self()) {
                    $('#driver_update_method input[value=1]').click();
                } else {
                    $('#driver_update_method input[value=2]').click();
                    check_multi_version = true;
                }
            }
        }
    });
}

// 平台相关的硬件驱动选项都 uncheck
function uncheck_all_conditional() {
    var api = $('#driver_version_tree').aciTree('api');
    var children = api.children(null, true, true);
    api.checkboxes(children).each(api.proxy(function (element) {
        var item = $(element),
            parent = api.parent(item);
        if (parent && api.itemData(parent).is_platform) { // 必须是平台相关的硬件
            if (api.itemData(item).Str_HWPlatform == '用户导入') {
                // 用户导入驱动不受影响
            } else {
                this.uncheck(item);
            }
        }
    }, true));
}

// 所有 uncheck
function uncheck_all() {
    var api = $('#driver_version_tree').aciTree('api');
    var children = api.children(null, true, true);
    api.checkboxes(children).each(api.proxy(function (element) {
        var item = $(element);
        this.uncheck(item);
    }, true));
}

// 是否是源机
function is_source_machine_from_ui(treeid) {
    var api = $('#' + treeid).aciTree('api');
    var children = api.children(null, true, true);
    var rs = false;
    api.radios(children, true).each(api.proxy(function (element) {
        console.log(api.itemData($(element)))
        if (api.itemData($(element)).source_machine) {
            rs = true;
        } else {
            rs = false;
        }
        return false
    }, true));

    return rs;
}

// 给每个根加入 force install choice 选择
// 将强制安装的 驱动选择
function append_force_install_button($tree) {
    var api = $tree.aciTree('api');
    // 加入强制安装的按钮
    api.children(null, false, true).each(function (index, element) {
        if (api.itemData($(element)).is_force_install) {
            var html = '<span class="force-install-entry"><label for=force-install"' + index + '">' +
                '<input type="checkbox" checked id=force-install"' + index + '">强制安装<label></span>';
        } else {
            var html = '<span class="force-install-entry"><label for=force-install"' + index + '">' +
                '<input type="checkbox" id=force-install"' + index + '">强制安装<label></span>';
        }
        $(element).find('.aciTreeEntry:first').append($(html));
    });
    // 监听事件
    $('.force-install-entry input').change(function () {
        var root_li = $(this).parents('li:first');
        if ($(this).is(':checked')) {
            api.children(root_li, true, true).each(function (index, element) {
                var item = $(element);
                api.removeCheckbox(item);
                api.addRadio(item);
            });
        } else {
            api.children(root_li, true, true).each(function (index, element) {
                var item = $(element);
                api.removeRadio(item);
                api.addCheckbox(item);
            });
        }
    });
}
