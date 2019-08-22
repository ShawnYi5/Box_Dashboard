var g_adapter_list = null;
var g_adapter_list_or = null;
var is_to_self = false;
// 如果是原机还原，则把原机的网络显示出来，并且默认是不可以编辑的；
function add_ip_content_to_set_ip_div(obj_list) {

    var dns_list = [];
    var gate_way = '';

    $.each(obj_list, function (i, item) {
        var new_content = $('#ip_tmp_data .adapter_container').clone(),
            mac_str = get_mac_str(item.target_nic.szMacAddress),
            key_adapter = false, //关键网卡
            is_dhcp = item.src_is_dhcp,
            dhcp_str = is_dhcp ? '(DHCP)' : '';

        new_content.find('input').val('');
        if (item.target_nic.isConnected) {
			new_content.find('.adapter').text(item.target_nic.szDescription + '(' + mac_str + ')').addClass('connected');
            key_adapter = true;
            is_to_self = item['is_to_self'];
            // 本机还原且是DHCP 则显示警告的提示
            if (is_to_self && is_dhcp) {
                new_content.find('.to_self_and_dhcp').show();
            }
        }
        else {
			new_content.find('.adapter').text(item.target_nic.szDescription + '(' + mac_str + ')');
            key_adapter = false;
        }
        new_content.find('.adapter_info').val(item.target_nic.szGuid).attr('data-index', i); //在 g_adapter_list  索引
        $.each(item.ip_mask_pair, function (index, info) {
            if (index == 0) {
                if (is_to_self && is_dhcp && !key_adapter) {
                    new_content.find('.ip').val(info.Ip).after('<span class="dhcp_span">(DHCP)</span>');
                } else {
                    new_content.find('.ip').val(info.Ip);
                }
                new_content.find('.mask').val(info.Mask);
            } else {
                add_item(new_content.find('.add_ip').click()); // 新增加一对
                if (is_to_self && is_dhcp && !key_adapter) {
                    new_content.find('.ip:last').val(info.Ip).after('<span class="dhcp_span">(DHCP)</span>');
                } else {
                    new_content.find('.ip:last').val(info.Ip);
                }
                new_content.find('.mask:last').val(info.Mask);
            }
        });
        if (gate_way == '' && item.gate_way != '' && item.gate_way != '0.0.0.0') {
            gate_way = item.gate_way;
        }
        $.merge(dns_list, item.dns_list);
        new_content.appendTo('#ipsetdiv .set_ip_div .ip_container');

    });
    // 去除重复的dns
    $.uniqueSort(dns_list);

    var new_dns_gateway_content = $('#dns_gate_way_data .adapter_container').clone();
    new_dns_gateway_content.find('.gateway').val(gate_way);
    $.each(dns_list, function (index1, dns) {
        if (index1 == 0) {
            new_dns_gateway_content.find('.dns').val(dns);
        } else {
            add_item(new_dns_gateway_content.find('.add_dns').click()); // 新增加一对
            new_dns_gateway_content.find('.dns:last').val(dns);
        }
    });
    new_dns_gateway_content.appendTo('#ipsetdiv .set_ip_div .dns_gateway_container');

    // 本机还原
    if (is_to_self) {
        $('#ipsetdiv .should_check_wrap').show(); // 展示可以切换的按钮
        disable_content('#ipsetdiv .set_ip_div', true); //所有内容不可以编辑
    }
    /*
    if(!src_is_linux()){
        $('#ipsetdiv input[name="nic_name"]').attr('disabled', true);
    }
    */

}

// 把源的网络显示到 容器中 仅作为显示
function add_ip_content_to_just_show(obj_list) {
    var dns_list = [];
    var gate_way = '';

    $.each(obj_list, function (i, item) {
        var new_content = $('#ip_tmp_data .adapter_container').clone(),
            mac_str = get_mac_str(item.Mac),
            is_dhcp = (item.Dhcp == undefined || item.Dhcp == 0) ? false : true;

        new_content.find('input').val('');
        new_content.find('.adapter').html(item.Description +'[<span style="color:#20a0ff;">'+ item.Name +'</span>](' + mac_str + ')');
        $.each(item.IpAndMask, function (index, info) {
            if (index == 0) {
                if (is_dhcp) {
                    new_content.find('.ip').val(info.Ip).after('<span>(DHCP)</span>');
                }
                else {
                    new_content.find('.ip').val(info.Ip);
                }
                new_content.find('.mask').val(info.Mask);
            } else {
                add_item(new_content.find('.add_ip').click()); // 新增加一对
                if (is_dhcp) {
                    new_content.find('.ip:last').val(info.Ip).after('<span>(DHCP)</span>');
                }
                else {
                    new_content.find('.ip:last').val(info.Ip);
                }
                new_content.find('.mask:last').val(info.Mask);
            }
        });
        if (gate_way == '' && item.GateWay != '' && item.GateWay != '0.0.0.0') {
            gate_way = item.GateWay;
        }
        $.merge(dns_list, item.Dns);
        new_content.appendTo('#ipsetdiv .just_show_src .ip_container');

    });
    // 去除重复的dns
    $.uniqueSort(dns_list);

    var new_dns_gateway_content = $('#dns_gate_way_data .adapter_container').clone();
    new_dns_gateway_content.find('.gateway').val(gate_way);
    $.each(dns_list, function (index1, dns) {
        if (index1 == 0) {
            new_dns_gateway_content.find('.dns').val(dns);
        } else {
            add_item(new_dns_gateway_content.find('.add_dns').click()); // 新增加一对
            new_dns_gateway_content.find('.dns:last').val(dns);
        }
    });
    new_dns_gateway_content.appendTo('#ipsetdiv .just_show_src .dns_gateway_container');
    disable_content('#ipsetdiv .just_show_src', true);
}

$('#ipsetdiv').on('click', '.should_check', function () {
    if (is_to_self) {
        var checked = $(this).is(':checked');
        if (checked) {
            disable_content('#ipsetdiv .set_ip_div', false);
            $('div#ipsetdiv .to_self_and_dhcp').hide();
            $('div#ipsetdiv .dhcp_span').hide();
        } else {
            $('div#ipsetdiv .set_ip_div .ip_container').empty();
            $('div#ipsetdiv .set_ip_div .dns_gateway_container').empty();
            add_ip_content_to_set_ip_div(g_adapter_list_or);
        }
        /*
        if(!src_is_linux()){
            $('#ipsetdiv input[name="nic_name"]').attr('disabled', true);
        }
        */
    } else {
        return;
    }
})

function GetAdapters(jsonstr) {
    $('div#ipsetdiv').html('');
    $('.mywaite').hide();
    if (jsonstr.r != 0) {
        openErrorDialog({title: '错误', html: jsonstr.e});
        return;
    }

    $('div#ipsetdiv').html('');
    $('div#ipsetdiv').append($('#tmp_adapter_container div:first').clone());

    // 展示路由信息
    show_routers_to_page(jsonstr);

    g_adapter_list = jsonstr.list;
    g_adapter_list_or = $.extend(true, [], g_adapter_list);
    var src_adapter_info = jsonstr.src_nics;
    add_ip_content_to_set_ip_div(g_adapter_list_or); //添加到 设置IP容器
    if(src_adapter_info.length !==0){
        add_ip_content_to_just_show(src_adapter_info);//添加到 显示源信息
    }
    else {
        $('.ip_set_header li:last').hide();
    }
}

// 切换目标容器内的 输入 是否可编辑
function disable_content(container, is_disabled) {
    $(container).find('input').attr('disabled', is_disabled);
    if (is_disabled) {
        $(container).find('input').attr('disabled', true);
        $(container).find('.text_button').hide();
    }
    else {
        $(container).find('input').attr('disabled', false);
        $(container).find('.text_button').show();
    }
}

function add_item(obj, to_select) {
    var container = $(obj).parents('.adapter_container').find(to_select),
        current_entry = container.children('.control_group:first')
    new_entry = $(current_entry.clone()).appendTo(container);
    new_entry.find('span').remove();
    new_entry.find('input').val('');
    new_entry.find('.gate_way').hide();
    new_entry.find('input:last').after($('<span class="text_button not_float del"' +
        ' onclick="remove_item(this)">删除</span>'));
}

function remove_item(obj) {
    $(obj).parents('.control_group').remove();
}

$('#ipsetdiv').on('click', 'li', function () {
    $(this).siblings().removeClass('ip-choice');
    $(this).addClass('ip-choice');
})

function show_div(select) {
    $('#ipsetdiv ' + select).show();
    $('#ipsetdiv ' + select).siblings('div').hide();
    if (is_to_self) {
        if (select == '.just_show_src') {
            $('.should_check_wrap').hide();
        } else {
            $('.should_check_wrap').show();
        }
    }
}

function get_mac_str(mac) {
    if (mac.length == 12) {
        var mac_str = '';
        for (var i = 0; i < mac.length; i++) {
            if (i % 2 == 0 && i != 0) {
                mac_str += ':' + mac[i];
            } else {
                mac_str += mac[i];
            }
        }
        return mac_str.toLocaleUpperCase();
    }
    else {
        return mac.replace(/-/g, ':').toLocaleUpperCase()
    }
}

// 检测是否填写的是否是合法的ip
// 主网卡必须至少填写IP
// 其它网卡是不需要填写的
function check_and_update_g_adapter_list() {
    if (g_adapter_list == null) {
        openErrorDialog('错误', '服务器内部错误，未获取到网卡信息。');
        return false;
    }


    if ($('#ipsetdiv .set_ip_div .adapter_container').length == 0) {
        openErrorDialog('错误', '没有发现网卡设备。');
        return false;
    }

    var is_valid = true;
    $.each($('#ipsetdiv .set_ip_div .ip2mask'), function (i, v) {
        var ip = $(this).find('.ip').val(),
            mask = $(this).find('.mask').val();
        if (ip == '' && mask == '') {
            return true; // like continue
        }
        if (ip != '' && !isIPV4(ip)) {
            openErrorDialog('错误', '请输入正确的IP地址，' + '[ ' + ip + ' ]' +
                '不符合规范。');
            is_valid = false;
            return false; // like break
        }
        if (mask != '' && !isMask(mask)) {
            openErrorDialog('错误', '请输入正确的子网掩码，' + '[ ' + mask + ' ]' +
                '不符合规范。');
            is_valid = false;
            return false;
        }
        if (ip == '' || mask == '') {
            openErrorDialog('错误', 'IP地址和子网掩码只能成对出现，' +
                '请填写完整或两者均不填写。');
            is_valid = false;
            return false;
        }
    })
    if (!is_valid) {
        return false
    }
    $.each($('#ipsetdiv .set_ip_div .gateway,.dns'), function (i, v) {
        if ($(this).val() != '' && !isIPV4($(this).val())) {
            var label = $(this).siblings('label').text().slice(0, -1);
            openErrorDialog('错误', '请输入正确的' + label + '，' + '[ ' + $(this).val() + ' ]' +
                '不符合规范。');
            is_valid = false;
            return false;
        }
    })
    if (!is_valid) {
        return false
    }

    // 检测主控制 网卡 是否有IP
    var found_key_adapter = false;
    $.each($('#ipsetdiv .set_ip_div .adapter_container'), function (i, v) {
        var is_key_adapter = $(this).find('.adapter').hasClass('connected');
        if (is_key_adapter) {
            found_key_adapter = true;
            var set_valid_ip = false;
            $.each($(this).find('.ip2mask'), function () {
                var ip = $(this).find('.ip').val(),
                    mask = $(this).find('.mask').val();
                if (ip == '' && mask == '') {
                    return true; // like continue
                }
                if (isIPV4(ip) && isMask(mask)) {
                    set_valid_ip = true;
                    return false; //break
                }
            });
            if (!set_valid_ip) {
                openErrorDialog('错误', '请至少在主控制网卡上配置一组IP。')
                is_valid = false;
                return false; //like
            }
        }
    })

    if (!found_key_adapter) {
        openErrorDialog('错误', '没有发现主控制网卡')
        return false;
    }

    if (!is_valid) {
        return false
    }

    // 检测网关可达
    var gate_way = $('#ipsetdiv .set_ip_div .gateway').val();
    if (isIPV4(gate_way)){
        var reachable = false;
        $.each($('#ipsetdiv .set_ip_div .ip2mask'), function (i, v) {
            var ip = $(this).find('.ip').val(),
                mask = $(this).find('.mask').val();
            if (gateway_reachable(ip, mask, gate_way)){
                reachable = true;
                return false; //break
            }
        })
        if(!reachable){
            var msg = '操作失败， 默认网关[ '+gate_way+' ]不在由IP地址和子网掩码定义的同一网络段（子网）上。';
            openErrorDialog('错误', msg);
            return false;
        }
    }

    // 检测所有网卡名字是否冲突
    var nic_names = [],
        is_valid = true;
    $.each($('#ipsetdiv .set_ip_div input[name="nic_name"]'), function (i, v) {
        var nic_name = $(v).val();
        if (!nic_name){
            return true; // continue
        }else {
            if (nic_names.indexOf(nic_name) != -1){
                is_valid = false;
                openErrorDialog('错误', '重复的网卡名：'+nic_name);
                return false; // break
            }else {
                nic_names.push(nic_name);
            }
        }
    })

    if (!is_valid) {
        return false
    }


    //  备机还原且没有点击编辑按钮
    if (is_to_self && !$('#ipsetdiv .should_check').is(':checked')) {
        g_adapter_list = $.extend(true, [], g_adapter_list_or);
        $.each(g_adapter_list, function (index, nic) {
            nic.is_set = nic.ip_mask_pair.length !== 0;
        });

        return true;
    }

    var dns_list = [];
    $.each($('#ipsetdiv .set_ip_div .dns'), function (i ,e) {
        var v = $(e).val();
        if (v != '') {
            dns_list.push(v);
        }
    })
    $.uniqueSort(dns_list);
    // update g_adapter_list
    $.each($('#ipsetdiv .set_ip_div .ip_container .adapter_container'), function (i, v) {
        var inner_index = $(this).find('.adapter_info').attr('data-index'),
            ip_list = [],
            mask_list = [],
            is_set = false;

        $(this).find('.ip').each(function (i, e) {
            var v = $(e).val();
            if (v != '') {
                ip_list.push(v);
                is_set = true
            }
        });

        $(this).find('.mask').each(function (i, e) {
            var v = $(e).val();
            if (v != '') {
                mask_list.push(v);
                is_set = true
            }
        });

		var nic_name = null;
		$(this).find("input[name='nic_name']").each(function(i,e){
			nic_name = $(e).val();
		});

        g_adapter_list[inner_index].ip_mask_pair = ip_list.map(function (e, i) {
            return {Ip: ip_list[i], Mask: mask_list[i]}
        })
        g_adapter_list[inner_index].dns_list = is_set ? dns_list : [];
        g_adapter_list[inner_index].gate_way = is_set ? gate_way : '';
        g_adapter_list[inner_index].is_set = is_set;
        g_adapter_list[inner_index].is_to_self = false;
		if(nic_name)
		{
			g_adapter_list[inner_index].name = nic_name;
		}else{
		    g_adapter_list[inner_index].name = null;
        }
    })

    return true;
}
