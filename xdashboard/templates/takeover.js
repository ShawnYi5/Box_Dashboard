function gen_mac()
{
	$('div.class_kvm_adpter').each(function(){
		var mac = $(this).find("input[name='kvm_mac']").val();
		if (mac=='')
		{
			mac = "00-XX-XX-XX-XX-XX".replace(/X/g, function() {
			  return "0123456789ABCDEF".charAt(Math.floor(Math.random() * 16))
			});
			$(this).find("input[name='kvm_mac']").val(mac);
		}
	})
}

function addIPButton(obj)
{
	var html = $(obj).prev().prop('outerHTML');
	$(obj).prev().after(html);
}

function addAdpterButton(obj)
{
	var html = $(obj).parent().prev().prop('outerHTML');
	$(obj).parent().prev().after(html);
	gen_mac();
}

function addRouteButton(obj)
{
	var html = $(obj).parent().prev().prop('outerHTML');
	var txt = $($(html).find('div')[0]).html();
	var i = parseInt(txt.replace('路由',''))+1;
	html = html.replace(txt,'路由'+i);
	$(obj).parent().prev().after(html);
	
}

function addDNSButton(obj)
{
	var html = $(obj).parent().prev().prop('outerHTML');
	var txt = $($(html).find('span')[0]).html();
	var i = parseInt(txt) + 1;
	html = html.replace('<span>'+txt+'</span>','<span>'+i+'</span>');
	$(obj).parent().prev().after(html);
}

function get_kvm_adpter()
{
	var adpter = [];
	$('div.class_kvm_adpter').each(function(){
		if ($(this).is(':hidden'))
		{
			return true;
		}
		var ips = [];
		var kvm_mac = $(this).find("input[name='kvm_mac']").val();
		var nic_name = $(this).find("input[name='nic_name']").val();
		var network_card = $(this).find("select[name='adpter_name']").val();
		if (network_card == "-1")
		{
			return true;
		}
		var vlan_no_obj = $(this).find("input[name='vlan_no']");
		var vlan_no = vlan_no_obj.val();
		if(vlan_no=='' || vlan_no_obj.prop('disabled'))
		{
			vlan_no = null;
		}
		for(var i=0;i<$(this).find('.class_kvm_ip').length;i++)
		{
			var obj = $(this).find('.class_kvm_ip')[i];
			var kvm_ip = $(obj).find("input[name='kvm_ip']").val();
			var kvm_mask = $(obj).find("input[name='kvm_mask']").val();
			if(isIPV4(kvm_ip) && isIPV4(kvm_mask))
			{
				ips.push({'ip':kvm_ip,'mask':kvm_mask});
			}
		}
		if(kvm_mac)
		{
			if(nic_name=='')
			{
				nic_name = null;
			}
			adpter.push({'name':network_card,'vlan_no':vlan_no,'nic_name':nic_name,'mac':kvm_mac,'ips':ips});
		}
	});
	return $.toJSON(adpter);
}

function get_kvm_route()
{
	var route = [];
	$('div.class_kvm_route').each(function(){
		var kvm_route_ip = $(this).find("input[name='kvm_route_ip']").val();
		var kvm_route_mask = $(this).find("input[name='kvm_route_mask']").val();
		var kvm_route_gate = $(this).find("input[name='kvm_route_gate']").val();
		if(isIPV4(kvm_route_ip) && isIPV4(kvm_route_mask) && isIPV4(kvm_route_gate))
		{
			route.push({'ip':kvm_route_ip,'mask':kvm_route_mask,'gateway':kvm_route_gate});
		}
	});
	return $.toJSON(route);
}

function get_kvm_gateway()
{
	var gateway = [];
	gateway.push($('div.class_kvm_dns').find('input[name=kvm_gateway]').val());
	return $.toJSON(gateway);
}

function get_kvm_dns()
{
	var dns_arr = [];
	$('div.class_kvm_dns').find("input[name='kvm_dns']").each(function(){
		var dns = $(this).val();
		if(isIPV4(dns))
		{
			dns_arr.push(dns);
		}
	});

	return $.toJSON(dns_arr);
}

function kvm_checkdata()
{
	var kvm_name = $("#kvm_name").val();
	if(isEmpty(kvm_name))
	{
		openErrorDialog({title:'错误',html:'名称不能为空'});
		return false;
	}else if(kvm_name.length > 255)
	{
		openErrorDialog({title:'错误',html:'名称长度不能超过100'});
		return false;
	}
	if($("#kvm_memory_size").val().indexOf(".")!=-1)
	{
		openErrorDialog({title:'错误',html:'内存大小应为正整数'});
		return false;
	}
	var kvm_memory_size = parseInt($("#kvm_memory_size").val());
	if(kvm_memory_size<=0)
	{
		openErrorDialog({title:'错误',html:'内存大小应大于等于1'});
		return false;
	}
	var kvm_storagedevice = $("#kvm_storagedevice").val();
	if(kvm_storagedevice == -1)
	{
		openErrorDialog({title:'错误',html:'请选择缓存数据存储设备'});
		return false;
	}
	var bret = true;
	$('div.class_kvm_adpter').each(function(){
		var kvm_mac = $(this).find("input[name='kvm_mac']").val();
		var network_card = $(this).find("select[name='adpter_name']").val();
		for(var i=0;i<$(this).find('.class_kvm_ip').length;i++)
		{
			var obj = $(this).find('.class_kvm_ip')[i];
			var kvm_ip = $(obj).find("input[name='kvm_ip']").val();
			var kvm_mask = $(obj).find("input[name='kvm_mask']").val();
			if(kvm_ip!='' || kvm_mask!='')
			{
				if (!isIPV4(kvm_ip))
				{
					openErrorDialog({title:'错误',html:'IP不正确'});
					bret = false;
					return false;
				}
				if (!isMask(kvm_mask))
				{
					openErrorDialog({title:'错误',html:'网卡子网掩码不正确'});
					bret = false;
					return false;
				}
			}
		}
	});

	if(bret == false)
	{
		return false;
	}

	$('div.class_kvm_route').each(function(){
		var kvm_route_ip = $(this).find("input[name='kvm_route_ip']").val();
		var kvm_route_mask = $(this).find("input[name='kvm_route_mask']").val();
		var kvm_route_gate = $(this).find("input[name='kvm_route_gate']").val();
		if(kvm_route_ip!='' || kvm_route_mask!='' || kvm_route_gate!='' )
		{
			if(!isIPV4(kvm_route_ip))
			{
				openErrorDialog({title:'错误',html:'目标网络不正确'});
				bret = false;
				return false;
			}

			if(!isIPV4(kvm_route_mask))
			{
				openErrorDialog({title:'错误',html:'路由子网掩码不正确'});
				bret = false;
				return false;
			}

			if(!isIPV4(kvm_route_gate))
			{
				openErrorDialog({title:'错误',html:'路由网关不正确'});
				bret = false;
				return false;
			}
		}
	});

	if(bret == false)
	{
		return false;
	}

	var kvm_gateway = $('div.class_kvm_dns').find('input[name=kvm_gateway]').val();
	if(kvm_gateway!='')
	{
		if(!isIPV4(kvm_gateway))
		{
			openErrorDialog({title:'错误',html:'默认网关不正确'});
			return false;
		}
	}

	$('div.class_kvm_dns').find("input[name='kvm_dns']").each(function(){
		var dns = $(this).val();
		if(dns!='')
		{
			if(!isIPV4(dns))
			{
				openErrorDialog({title:'错误',html:'DNS不正确'});
				bret = false;
				return false;
			}
		}
	});

	if(bret == false)
	{
		return false;
	}

	return true;
}

function deladpter(obj)
{
	if($('div.class_kvm_adpter').length==2)
	{
		return;
	}
	openConfirmDialog({
		"title":"删除",
		"html":'确定删除网卡？',
		height: 200,
		width: 274,
		onBeforeOK: function(){
			$(obj).parent().parent().remove();
			$(this).dialog('close');
		}
	});
	
}

function delroute(obj)
{
	if($('div.class_kvm_route').length==2)
	{
		return;
	}
	openConfirmDialog({
		"title":"删除",
		"html":'确定删除路由？',
		height: 200,
		width: 274,
		onBeforeOK: function(){
			$(obj).parent().parent().remove();
			$(this).dialog('close');
		}
	});
	
}

$('#advancedsettingbtn').click(function(){
	var tbody = $(this).parent().siblings();
    if (tbody.is(':visible')){
        tbody.hide('blind', 800);
        $(this).text('►高级设置');
    }
    else{
        tbody.show('blind', 800);
        $(this).text('▼高级设置');
    }
});

$('#kvm_cpu_sockets,#kvm_cpu_cores').change(function(){
	var kvm_cpu_count = $('#kvm_cpu_sockets').val()*$('#kvm_cpu_cores').val();
	$('#kvm_cpu_count').html(kvm_cpu_count);
});

function adpter_name_change(obj){
	if($(obj).val()=='private_aio' || $(obj).val().indexOf('bond')==0)
	{
		$(obj).parent().siblings(".vlan_no").children("input[name='vlan_no']").prop('disabled',true);
	}
	else
	{
		$(obj).parent().siblings(".vlan_no").children("input[name='vlan_no']").prop('disabled',false);
	}
}