package kr.co.sample.web;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import kr.co.sample.service.UserService;

@Controller
@RequestMapping("/user")
public class UserController {

    private UserService userService;

    /**
     * 사용자 목록 조회
     * @param model 모델
     */
    @RequestMapping(value = "/list.do", method = RequestMethod.GET)
    public String selectUserList(ModelMap model) {
        model.addAttribute("list", userService.selectUserList());
        return "user/userList";
    }

    // 사용자 등록 처리
    @RequestMapping(value = "/insert.do", method = RequestMethod.POST)
    public String insertUser(UserVO vo) {
        userService.insertUser(vo);
        return "redirect:/user/list.do";
    }

    @RequestMapping("/detail.do")
    public String selectUser(String userId) {
        return "user/userDetail";
    }
}
