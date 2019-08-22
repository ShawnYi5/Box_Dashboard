function CreateBackupCallback(jsonstr,immediately)
{
	var curPage = '{{ page }}';
	if(jsonstr.r!=0)
	{
		try
		{
			var new_url = '../autoverifytask_handle/?a=get_task_list';
			$('#list').setGridParam({url:new_url});
			$('#list').trigger("reloadGrid",[{page:1}]);
		}
		catch (e)
		{
		}
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	InitBackupControlData();
	try
	{
		$("#newbackupFormDiv").dialog('close');
	}
	catch (e)
	{
	}
	try
	{
		var new_url = '../autoverifytask_handle/?a=get_task_list';
		$('#list').setGridParam({url:new_url});
		$('#list').trigger("reloadGrid",[{page:1}]);
	}
	catch (e)
	{
	}

}


function InitBackupControlData()
{
	$( "#tabs" ).tabs( "option", "active", 0 );
	myAjaxGet('../backup_handle/','a=getstoragedevice',Getstoragedevice);
	$('#taskid').val('');
	$("#sel_storagedevice").val('');
	if(!isInEdit()){
		$('#verifytaskname').val('');
	}
	$('#stime0').val() || $('#stime0').val(curTime);
	$('#stime1').val() || $('#stime1').val(curTime);
	$('#stime2').val() || $('#stime2').val(curTime);
	$('#stime3').val() || $('#stime3').val(curTime);
	$('#stime4').val() || $('#stime4').val(curTime);
	$("div.myoneday").removeClass("myonedayselected");
	$(".prev").hide();
	$('#next').attr("value","下一步»");
	$("input[name=bakschedule][value=1]").prop("checked",true);
	$("input[name=perweek]").prop("checked",false);
	$('#bakschedule3').hide();
	$('#bakschedule4').hide();
	$('#bakschedule5').hide();
	$('#usemaxbandwidth').prop('checked',true);
	$('#maxbandwidth').val(300);

	$('#enable-backup-retry').prop('checked',true);
	$('#retry-counts').val(5);
	$('#retry-interval').val(3);

	init_shell_infos_from_ui($('#tabs-shell'));
}

function verify_interval_day_hour_min(interval) {
	interval = parseInt(interval);
	var unit = $('#interval-unit').find(':selected').val();
	if(unit === 'day'){
		if(interval > 365 || interval < 1){
			return '请输入正确的间隔天数(1-365)'
		}
	}
	else if (unit === 'hour'){
		if(interval < 1){
			return '请输入正确的间隔小时(最小1小时)'
		}
	}
	else if (unit === 'min'){
        if(interval < 5){
			return '请输入正确的间隔分钟(最小5分钟)'
		}
	}
	else {
		return '单位错误: ' + unit
	}

	return 'ok';
}

// 点击下一步时， 校验数据
function CheckData(selindex)
{
	if(selindex==0)
	{
		var taskname=$('#verifytaskname').val();
		if(taskname=='')
		{
			var tmphtml = '计划名称不能为空。';
			openErrorDialog({title:'错误',html:tmphtml});
			return false;
		}
		if( $(".storagedevicediv").is(":visible") && $("#storagedevice").val() == null  )
		{
			openErrorDialog({title:'错误',html:'选择临时存储设备。'});
			return false;
		}
		if( $(".storagedevicediv").is(":visible") && $("#storagedevice").val() == -1  )
		{
			openErrorDialog({title:'错误',html:'请先分配存储设备。'});
			return false;
		}
		if( $('#kvm_memory_size').val()=='')
		{
			openErrorDialog({title:'错误',html:'请填写内存大小'});
			return false;
		}
	}
	else if(selindex==1)
	{
		var backupschedule=$("input[name='bakschedule']:checked").val();
		$('#confirm_cdpperiod').html('-');
		$('#confirm_cdptype').html('-');
		if(backupschedule==2)
		{
			if($("#stime1").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
		}
		else if(backupschedule==3)
		{
			if($("#stime2").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
			if($("#timeinterval").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择间隔时间。'});
				return false;
			}
			if(!isNum($("#timeinterval").val()))
			{
				openErrorDialog({title:'错误',html:'请输入正确的间隔时间, 正整数。'});
				return false;
			}

			var msg = verify_interval_day_hour_min($("#timeinterval").val());
			if(msg !== 'ok'){
				openErrorDialog({title:'错误', html:msg});
				return false;
			}
		}
		else if(backupschedule==4)
		{
			if($("#stime3").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}

			if($("input[name='perweek']:checked").length==0)
			{
				openErrorDialog({title:'错误',html:'请勾选每周几备份。'});
				return false;
			}

		}

	}
	return true;
}

// 创建计划完成，并提交参数到后台
function OnFinish()
{
	var postdata='taskname='+encodeURIComponent($("#verifytaskname").val());
	postdata+='&storagedevice='+$("#storagedevice").val();
	var backupschedule=$("input[name='bakschedule']:checked").val();
	postdata+='&schedule='+backupschedule;
	postdata += '&kvm_memory_size=' + $("#kvm_memory_size").val();
    postdata += '&kvm_memory_unit=' + $("#kvm_memory_unit").val();
	if(backupschedule==2)
	{
		//仅备份一次
		postdata+='&starttime='+$("#stime1").val();
	}
	else if(backupschedule==3)
	{
		//每天
		postdata+='&starttime='+$("#stime2").val();
		postdata+='&timeinterval='+$("#timeinterval").val();
		postdata+='&intervalUnit=' + $('#interval-unit :selected').val();
	}
	else if(backupschedule==4)
	{
		//每周
		postdata+='&starttime='+$("#stime3").val();
		postdata+="&"+$("input[name='perweek']").serialize();
	}
	else if(backupschedule==5)
	{
		//每月
		postdata+='&starttime='+$("#stime4").val();
		for(var i=0;i<$("div.myoneday.myonedayselected").length;i++)
		{
			var monthly = $("div.myoneday.myonedayselected")[i].innerHTML;
			postdata+='&monthly='+monthly;
		}
	}

	if($('#verify_osname').prop('checked'))
	{
		postdata+='&verify_osname=1';
	}
	else
	{
		postdata+='&verify_osname=0';
	}
	if($('#verify_osver').prop('checked'))
	{
		postdata+='&verify_osver=1';
	}
	else
	{
		postdata+='&verify_osver=0';
	}
	if($('#verify_hdd').prop('checked'))
	{
		postdata+='&verify_hdd=1';
	}
	else
	{
		postdata+='&verify_hdd=0';
	}

	if($('#verify_last_point').prop('checked'))
	{
		postdata+='&last_point=1';
	}
	else
	{
		postdata+='&last_point=0';
	}

	if($('#script1').val()!==0)
	{
		postdata+='&script1='+$('#script1').val();
	}
	if($('#script2').val()!==0)
	{
		postdata+='&script2='+$('#script2').val();
	}
	if($('#script3').val()!==0)
	{
		postdata+='&script3='+$('#script3').val();
	}
	if($('#script4').val()!==0)
	{
		postdata+='&script4='+$('#script4').val();
	}
	if($('#script5').val()!==0)
	{
		postdata+='&script5='+$('#script5').val();
	}
	if($('#script6').val()!==0)
	{
		postdata+='&script6='+$('#script6').val();
	}
	if($('#script7').val()!==0)
	{
		postdata+='&script7='+$('#script7').val();
	}
	if($('#script8').val()!==0)
	{
		postdata+='&script8='+$('#script8').val();
	}

	if($('#taskid').val())
	{
		postdata+='&taskid='+$('#taskid').val();
		openConfirmDialog({
			title:'确认信息',
			html:'你确定要更改此项计划吗?',
			onBeforeOK:function(){
				postdata+="&a=createtask";
				myAjaxPost('../autoverifytask_handle/',postdata,CreateBackupCallback,0);
				$(this).dialog('close');
			}
		});
		return;
	}

	postdata+="&a=createtask";
	myAjaxPost('../autoverifytask_handle/',postdata,CreateBackupCallback,1);

}

function setPreControlStatus(selindex)
{
	if(selindex==1)
	{
		$(".prev").hide();
	}
	$('#next').attr("value","下一步»");
}

function setControlStatus(selindex)
{
	if(selindex==2) // 确认参数界面
	{
		$('#next').val('完成');
		var taskname=$('#verifytaskname').val();
		var storagedevice= $("#storagedevice").find("option:selected").text();
		var backuptype=1;
		$('#confirm_taskname').html(taskname);
		$('#confirm_storagedevice').html(storagedevice);
		var confirm_verify = $('<div></div>');
		confirm_verify.append('<div>操作系统能否启动</div>');
		confirm_verify.append('<div>网络状态</div>');
		if($('#verify_osname').prop('checked'))
		{
			confirm_verify.append('<div>客户端名称</div>');
		}
		if($('#verify_osver').prop('checked'))
		{
			confirm_verify.append('<div>操作系统版本</div>');
		}
		if($('#verify_hdd').prop('checked'))
		{
			confirm_verify.append('<div>硬盘（分区结构、容量、已使用量）</div>');
		}
		$('#confirm_verifytype').html(confirm_verify.html());
		var backupschedule=$("input[name='bakschedule']:checked").val();
		if(backupschedule==2)
		{
			schedule='仅验证一次，开始时间：'+$("#stime1").val();
		}
		else if(backupschedule==3)
		{
			schedule='按间隔时间，开始时间：'+$("#stime2").val()+'，每'+$("#timeinterval").val()+'{0}开始执行';
			schedule = schedule.replace('{0}', $('#interval-unit :selected').text());
		}
		else if(backupschedule==4)
		{
			var tmp=new Array();
			for(var i=0;i<$("input[name='perweek']:checked").length;i++)
			{
				var n=parseInt($("input[name='perweek']:checked")[i].value);
				switch(n)
				{
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
			schedule='每周，开始时间：'+$("#stime3").val()+'，每周'+tmp.join('、')+"备份";
		}
		else if(backupschedule==5)
		{
			var tmp=new Array();
			for(var i=0;i<$("div.myoneday.myonedayselected").length;i++)
			{
				tmp.push($("div.myoneday.myonedayselected")[i].innerHTML);
			}
			schedule='每月，开始时间：'+$("#stime4").val()+'，每月'+tmp.join(',')+"日备份";
		}
		$('#confirm_schedule').html(schedule);
		
	}
}

$('#prev').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	$( "#tabs" ).tabs( "option", "active", selindex-1 );
	setPreControlStatus(selindex);

});

function my_next(selindex) {
    $("#tabs").tabs("option", "active", selindex + 1);
    setControlStatus(selindex);
    if (selindex == 0) {
        $(".prev").show();
    }
}

function get_user_script_list_callback(jsonobj)
{
	$('.script').empty();
	$('.script').append('<option value=0 selected="selected" >可选项</option>');
	for(var i=0;i<jsonobj.length;i++)
	{
		$('.script').append('<option value="'+jsonobj[i].id+'" >'+jsonobj[i].name+'</option>');
	}
}

$('#next').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	if(selindex==1)
	{
		myAjaxGet('../autoverifytask_handle/','a=get_user_script_list',get_user_script_list_callback);
	}
	if(selindex==3)
	{
		OnFinish();
		return;
	}
	if(CheckData(selindex))
	{
		my_next(selindex);
	}
});


function Getstoragedevice(retjson)
{
	$("#storagedevice").empty();
	$.each(retjson, function(i, item){
		var free_GB = (item.free / Math.pow(1024, 1)).toFixed(2);
        free_GB = (item.value == -1) ? 0 : free_GB;

		var html="（可用：{3}）";
		html = html.replace('{3}', free_GB+'GB');

		if($('#sel_storagedevice').val()==item.value)
		{
			$("#storagedevice").append("<option value='"+item.value+"'  selected=\"selected\" >"+item.name+"</option>");
		}
		else
		{
			$("#storagedevice").append("<option value='"+item.value+"'>"+item.name+html+"</option>");
		}
	});
}


$("input[name='bakschedule']").click(function() {
	if(this.value==1)
	{
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==2)
	{
		$('#bakschedule2').slideDown(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==3)
	{
		$('#bakschedule3').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==4)
	{
		$('#bakschedule4').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==5)
	{
		$('#bakschedule5').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
	}

});

function GetServerInfoCallback(jsonstr)
{
	if(jsonstr.r!=0)
	{
		$('#show_servername').html(jsonstr.e);
		return;
	}
	$('#show_servername').html(jsonstr.servername);
	$('#show_pcname').html(jsonstr.pcname);
	$('#show_ip').html(jsonstr.ip);
	$('#show_mac').html(jsonstr.mac);
	$('#show_os').html(jsonstr.os);
	$('#show_buildnum').html(jsonstr.buildnum);
	$('#show_harddisknum').html(jsonstr.harddisknum);
	$('#show_harddiskinfo').html(jsonstr.harddiskinfo);
	$('#show_total').html(jsonstr.total + 'GB');
	$('#show_use').html(jsonstr.use + 'GB');
}

$(function()
{
	$('#bakschedule3').hide();
	$('#bakschedule4').hide();
	$('#bakschedule5').hide();
	for(var i=1;i<=31;i++)
	{
		$('#dayselect').append('<div class="myoneday">'+i+'</div>');
	}

	$("div.myoneday").click(function(){
		if($(this).hasClass("myonedayselected"))
		{
			$(this).removeClass("myonedayselected");
		}
		else
		{
			$(this).addClass("myonedayselected");
		}
	});
});


String.prototype.endWith=function(endStr){
  var d=this.length-endStr.length;
  return (d>=0&&this.lastIndexOf(endStr)==d);
};

function checkParent(api, child_item) {
	var parent = api.parent(child_item);
	if(!api.isChecked(parent)){
		api.check(parent);
	}
}


function isInEdit() {
	return window.IdOfChangePlan!=undefined;
}
